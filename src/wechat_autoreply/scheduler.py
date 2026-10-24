from .models import Alignment, messages_from, messages_json


def align_messages(previous, current):
    if not previous:
        return Alignment(tuple(current))
    before = [m.token() for m in previous]
    after = [m.token() for m in current]
    if before == after:
        return Alignment()
    matches = [k for k in range(1, min(len(before), len(after)) + 1)
               if before[-k:] == after[:k]]
    if len(matches) != 1:
        return Alignment(ambiguous=True, reason='消息尾部无法唯一对齐')
    return Alignment(tuple(current[matches[0]:]))


class Scheduler:
    def __init__(self, store, allowed=None, *, scope=None, campaigns=None):
        self.store = store
        self.allowed = set(allowed or ())
        self.scope, self.campaigns = scope, campaigns

    def allows(self, key):
        return self.scope.allows(key) if self.scope else key in self.allowed

    def enqueue_scan(self, candidates):
        # A later list observation can revoke a queued reply before AI or send.
        revoked = {c.key for c in candidates if self.allows(c.key) and c.muted is not False}
        with self.store.transaction():
            for c in candidates:
                if self.allows(c.key) and not c.unread and self.store.conversation(c.key):
                    self.store.update_conversation(c.key, preview='')
            for job in self.store.active_jobs():
                if job['conversation'] in revoked:
                    self.store.update_job(job['id'], status='skipped', draft=None,
                                          reason='muted_or_unknown')
        eligible = [c for c in candidates if c.unread and c.muted is False and
                    (c.identity_verified or (self.scope and self.scope.config.app_id!='wechat')) and self.allows(c.key)]
        for c in sorted(eligible, key=lambda c: c.position, reverse=True):
            if self.campaigns:
                self.campaigns.observe_incoming(c.key)
            self.store.ensure_conversation(c.key, c.label)
            conv = self.store.conversation(c.key)
            marker = c.preview + '\x1f' + str(c.unread_count)
            if conv['blocked'] or marker == conv['preview']:
                continue
            with self.store.transaction():
                for job in self.store.active_jobs():
                    if job['conversation'] == c.key:
                        self.store.update_job(job['id'], status='stale', reason='new_list_input')
                self.store.update_conversation(c.key, preview=marker, unread_count=c.unread_count)
                self.store.create_job(c.key, conv['version'], [], [])

    def block(self, key, reason, status='ambiguous'):
        with self.store.transaction():
            self.store.update_conversation(key, blocked=reason)
            for job in self.store.active_jobs():
                if job['conversation'] == key:
                    self.store.update_job(job['id'], status=status, reason=reason)

    def observe_chat(self, snapshot, *, own_send=False):
        key = snapshot.key
        if not self.allows(key):
            return None
        self.store.ensure_conversation(key)
        if snapshot.muted is not False:
            self.block(key, 'muted_or_unknown', 'skipped')
            return None
        if not snapshot.identity_verified or not snapshot.at_latest or snapshot.draft:
            self.block(key, 'identity_tail_or_manual_draft')
            return None
        conv = self.store.conversation(key)
        if conv['blocked']:
            return None
        previous = messages_from(conv['snapshot'])
        active = next((j for j in self.store.active_jobs() if j['conversation'] == key), None)
        if not previous:
            count = conv['unread_count']
            tail = []
            for m in reversed(snapshot.messages):
                if m.direction != 'incoming':
                    break
                tail.append(m)
            if own_send:
                appended = tuple(snapshot.messages)
            elif snapshot.initial_unread is not None:
                boundary = snapshot.initial_unread
                if (not boundary or any(m.direction!='incoming' for m in boundary) or
                        [m.token() for m in snapshot.messages[-len(boundary):]] != [m.token() for m in boundary]):
                    self.block(key,'unread_boundary_unknown')
                    return None
                appended = boundary
            else:
                if not count or count > len(tail):
                    self.block(key, 'unread_boundary_unknown')
                    return None
                appended = tuple(reversed(tail[:count]))
        else:
            aligned = align_messages(previous, snapshot.messages)
            if aligned.ambiguous:
                self.block(key, aligned.reason)
                return None
            appended = aligned.appended
        new = [m for m in appended if m.direction == 'incoming']
        if self.campaigns:
            if new:
                self.campaigns.observe_incoming(key)
            elif not own_send and any(m.direction=='outgoing' for m in appended):
                self.campaigns.observe_manual_outgoing(key)
            if active and active.get('kind') != 'reply':
                active = None
        if any(m.kind != 'text' for m in new):
            self.block(key, 'nontext_input')
            return None
        with self.store.transaction():
            self.store.update_conversation(key, snapshot=messages_json(snapshot.messages))
            self.store.append_messages(key, messages_json(appended))
            if not new:
                if active and active['needs_refresh']:
                    if active['new_messages'] and not appended:
                        self.store.update_job(active['id'], needs_refresh=0, messages=messages_json(snapshot.messages))
                    elif appended and any(m.direction == 'outgoing' for m in appended):
                        self.store.update_job(active['id'], status='handled', reason='outgoing_reply_observed')
                    else:
                        self.block(key, 'new_signal_without_unique_new_input')
                return active
            version = conv['version'] + 1
            self.store.update_conversation(key, version=version)
            if active and active['needs_refresh'] and not active['messages']:
                self.store.update_job(active['id'], version=version, messages=messages_json(snapshot.messages),
                                      new_messages=messages_json(new), needs_refresh=0)
                return self.store.job(active['id'])
            if active:
                self.store.update_job(active['id'], status='stale', reason='new_input')
            job = self.store.create_job(key, version, messages_json(snapshot.messages), messages_json(new))
            incoming_seen = False
            for message in appended:
                incoming_seen = incoming_seen or message.direction == 'incoming'
                if incoming_seen and message.direction == 'outgoing':
                    self.block(key, 'incoming_before_outgoing_requires_attention')
                    return None
            return job
