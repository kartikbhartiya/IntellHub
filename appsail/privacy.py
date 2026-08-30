"""Keep person names off the wire to third-party LLM providers.

The problem
-----------
The copilot answers questions by injecting real records into the prompt — named
offenders with their rowdy-sheeter status and gang, the highest-risk individual on
the watchlist by name — and POSTs that prompt to Groq or NVIDIA. Those are
third-party services outside India. Sending citizen and offender PII to them is a
cross-border transfer that a police deployment cannot make casually, and until now
it happened on every copilot message.

The approach
------------
Alias on the way out, restore on the way back.

Names the platform injects are replaced with stable per-request aliases
(`[PERSON_1]`, `[PERSON_2]`) before the prompt leaves the process. The model
reasons over the aliases perfectly well — it only needs referential consistency,
not the actual name — and the aliases are mapped back to real names locally before
the answer is shown. The provider sees structure and never sees a person.

This keeps the feature genuinely useful, which matters: a redaction that makes the
copilot useless would simply be turned off.

Limits, stated plainly
----------------------
* A name the *user types themselves* is passed through. That is their own input,
  and rewriting a query mid-flight would break the search it describes. Set
  `LLM_BLOCK_TYPED_NAMES=1` to refuse such queries outright instead.
* Aliasing hides identity, not the attributes attached to it. "[PERSON_1], 44
  prior cases, operates across 20 districts" is still a detailed record, and in a
  small enough population that is re-identifiable. This reduces exposure; it does
  not make the transfer risk-free.
* **It only covers the call sites that use it.** A code review found three prompt
  paths still sending names raw — the most-connected suspects in the briefing,
  missing-person names, and officer name plus badge number — months after this
  module's docstring claimed the provider "never sees a person". Any new code that
  interpolates a name into a prompt must call `mask()`; there is no interception
  layer that will do it for you.
* The alias map lives only for the request that created it.
"""
import os
import re

from flask import g

ALIAS_PATTERN = re.compile(r"\[PERSON_\d+\]")
BLOCK_TYPED_NAMES = os.environ.get("LLM_BLOCK_TYPED_NAMES", "").lower() in ("1", "true", "yes")


def _state():
    """Request-scoped alias tables. Falls back to a plain dict off-request."""
    try:
        if not hasattr(g, "_privacy_aliases"):
            g._privacy_aliases = {}      # real name -> alias
            g._privacy_reverse = {}      # alias -> real name
        return g._privacy_aliases, g._privacy_reverse
    except RuntimeError:
        return {}, {}


def mask(name):
    """Real name -> stable alias for this request. Non-names pass through."""
    if not name:
        return name
    name = str(name).strip()
    if not name or name.lower() in ("none", "nan", "unknown"):
        return name
    forward, reverse = _state()
    if name in forward:
        return forward[name]
    alias = f"[PERSON_{len(forward) + 1}]"
    forward[name] = alias
    reverse[alias] = name
    return alias


def restore(text):
    """Alias -> real name, applied to a model's answer before it is displayed."""
    if not text:
        return text
    _forward, reverse = _state()
    if not reverse:
        return text
    return ALIAS_PATTERN.sub(lambda m: reverse.get(m.group(0), m.group(0)), str(text))


def active_aliases():
    """How many people this request aliased — recorded in the audit entry."""
    forward, _reverse = _state()
    return len(forward)


def describe():
    return {
        "policy": "person names injected into LLM prompts are aliased before egress "
                  "and restored locally in the answer",
        "aliases_this_request": active_aliases(),
        "typed_names_blocked": BLOCK_TYPED_NAMES,
        "limits": "aliasing hides identity, not the attributes attached to it; a "
                  "user-typed name is passed through unless LLM_BLOCK_TYPED_NAMES is set",
    }
