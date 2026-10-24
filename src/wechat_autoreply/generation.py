from dataclasses import dataclass
from typing import Optional, Tuple

from .models import Message


@dataclass(frozen=True)
class GenerationRequest:
    kind: str
    messages: Tuple[Message, ...]
    new_messages: Tuple[Message, ...] = ()
    campaign_id: Optional[int] = None
    sequence: Optional[int] = None
    sent_in_round: Tuple[str, ...] = ()
    instruction: str = ''


@dataclass(frozen=True)
class GenerationResult:
    disposition: str
    reply: Optional[str] = None
    reason: str = ''


def build_task_context(request, limits):
    from .provider import ProviderError, build_context
    if request.kind == 'reply':
        messages = build_context(request.messages, request.new_messages, limits)
    elif request.kind in ('proactive_initial','proactive_followup'):
        if (type(request.campaign_id) is not int or request.campaign_id < 1 or
                type(request.sequence) is not int or request.sequence not in (1,2,3) or
                (request.kind == 'proactive_initial') != (request.sequence == 1) or request.new_messages):
            raise ProviderError('invalid_proactive_request')
        selected, size = [], sum(len(t) for t in request.sent_in_round)
        if size > limits.max_context_chars or len(request.sent_in_round)>limits.max_messages:
            raise ProviderError('round_context_too_large')
        for message in reversed(request.messages):
            if message.direction not in ('incoming','outgoing'):
                raise ProviderError('unknown_message_direction')
            if message.kind != 'text':
                continue
            if len(selected)+len(request.sent_in_round) >= limits.max_messages or size+len(message.text)>limits.max_context_chars:
                break
            selected.append({'role':'user' if message.direction=='incoming' else 'assistant','content':message.text})
            size += len(message.text)
        messages = list(reversed(selected))
    else:
        raise ProviderError('invalid_task_kind')
    return {'kind':request.kind,'messages':messages,
            'new_messages':[m.text for m in request.new_messages],
            'campaign_id':request.campaign_id,'sequence':request.sequence,
            'sent_in_round':list(request.sent_in_round),'instruction':request.instruction}
