from .models import SendResult, messages_from, messages_json
from .provider import normalize_reply
from .scheduler import align_messages


class Sender:
    def __init__(self, store, driver, allowed, mode='preview', generation=lambda: 0):
        self.store, self.driver = store, driver
        self.allowed, self.mode, self.generation = set(allowed), mode, generation

    def _valid(self, job, cancel):
        current = self.store.job(job['id'])
        conv = self.store.conversation(job['conversation'])
        head = self.store.head_job()
        return (not cancel.is_set() and current is not None and
                current['status'] == 'draft_ready' and not current['needs_refresh'] and
                job['conversation'] in self.allowed and not conv['blocked'] and
                current['version'] == conv['version'] == job['version'] and
                current['generation'] == self.generation() == job['generation'] and
                head is not None and head['id'] == job['id'])

    def _stale(self, job, snap):
        self.store.update_job(job['id'], status='stale', reason='new_input_or_tail_changed')
        return SendResult('stale', 'new_input_or_tail_changed', snap)

    @staticmethod
    def _sent_evidence(before, after, draft):
        if not after.identity_verified or not after.at_latest or getattr(after, 'send_failed', False):
            return False
        aligned = align_messages(before, after.messages)
        if aligned.ambiguous:
            return False
        outgoing = [m for m in aligned.appended if m.direction == 'outgoing']
        return len(outgoing) == 1 and outgoing[0].kind == 'text' and outgoing[0].text == draft

    def send(self, job, cancellation):
        if self.mode != 'send':
            return SendResult('preview')
        if not self._valid(job, cancellation):
            return SendResult('skipped', 'job_no_longer_sendable')
        draft = normalize_reply(job['draft'])
        attempt = None

        def execute(device):
            nonlocal attempt
            if not self._valid(job, cancellation):
                return SendResult('skipped', 'paused_or_stale')
            before = device.read_chat(job['conversation'])
            if not before.identity_verified or before.key != job['conversation']:
                self.store.update_job(job['id'], status='ambiguous', reason='identity')
                self.store.update_conversation(job['conversation'], blocked='identity')
                return SendResult('skipped', 'identity')
            if before.muted is not False:
                self.store.update_job(job['id'], status='skipped', reason='muted_or_unknown')
                return SendResult('skipped', 'muted_or_unknown')
            if before.draft:
                self.store.update_job(job['id'], status='ambiguous', reason='manual_draft')
                self.store.update_conversation(job['conversation'], blocked='manual_draft')
                return SendResult('skipped', 'manual_draft')
            aligned = align_messages(messages_from(job['messages']), before.messages)
            if not before.at_latest or aligned.ambiguous or aligned.appended:
                return self._stale(job, before)
            device.input_text(draft)
            typed = device.read_chat(job['conversation'])
            if (not typed.identity_verified or typed.key != job['conversation'] or
                    typed.muted is not False or typed.draft != draft):
                self.store.update_job(job['id'], status='ambiguous', reason='draft_or_identity_mismatch')
                self.store.update_conversation(job['conversation'], blocked='draft_or_identity_mismatch')
                return SendResult('skipped', 'draft_or_identity_mismatch')
            changed = align_messages(before.messages, typed.messages)
            if changed.ambiguous or changed.appended:
                device.clear_own_draft(draft)
                typed.draft = ''
                return self._stale(job, typed)
            if not self._valid(job, cancellation):
                return SendResult('skipped', 'paused')
            # Durable checkpoint precedes the one and only submit action.
            attempt = self.store.mark_sending(job['id'], draft, messages_json(typed.messages))
            device.submit()
            after = device.read_chat(job['conversation'])
            status = 'sent' if after.key == job['conversation'] and self._sent_evidence(typed.messages, after, draft) else 'uncertain'
            self.store.finish_attempt(attempt, status, '' if status == 'sent' else 'outgoing_not_uniquely_verified')
            # Scheduler must account for every newly appended incoming message
            # before advancing the conversation baseline over this observation.
            try:
                device.return_to_list()
            except Exception:
                return SendResult(status, 'return_to_list_needs_attention', after)
            return SendResult(status, snapshot=after)

        try:
            def guarded(device):
                result = execute(device)
                if result.status in ('skipped', 'stale', 'failed'):
                    try:
                        device.return_to_list()
                    except Exception:
                        return SendResult(result.status, 'return_to_list_needs_attention', result.snapshot)
                return result
            return self.driver.transaction(guarded, cancellation)
        except Exception:
            if attempt is not None:
                self.store.finish_attempt(attempt, 'uncertain', 'submit_or_verification_interrupted')
                return SendResult('uncertain', 'submit_or_verification_interrupted')
            self.store.update_job(job['id'], status='failed', reason='navigation_or_input_interrupted')
            self.store.update_conversation(job['conversation'], blocked='navigation_or_input_interrupted')
            return SendResult('failed', 'navigation_or_input_interrupted')

    def reconcile(self, attempt, cancellation=None):
        if attempt['status'] != 'uncertain':
            return SendResult('skipped')
        job = self.store.job(attempt['job_id'])
        if not job or job['conversation'] not in self.allowed:
            return SendResult('skipped', 'not_allowed')
        observed = []
        return_failed = False
        try:
            def read_only(device):
                snapshot = device.read_chat(job['conversation'])
                observed.append(snapshot)
                device.return_to_list()
                return snapshot
            snapshot = self.driver.transaction(read_only, cancellation)
        except Exception:
            if not observed:
                return SendResult('uncertain', 'read_failed')
            snapshot, return_failed = observed[0], True
        if snapshot.key == job['conversation'] and self._sent_evidence(messages_from(attempt['before_snapshot']), snapshot, attempt['draft']):
            self.store.finish_attempt(attempt['id'], 'sent', 'reconciled_from_unique_outgoing')
            self.store.update_conversation(job['conversation'], blocked='')
            return SendResult('sent', 'return_to_list_needs_attention' if return_failed else '', snapshot)
        return SendResult('uncertain', 'manual_confirmation_required')
