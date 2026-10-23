"""Conservative personal-request detection; it can request verification, never authorize data."""

import re

_SUBJECTS = (
    "account",
    "accounts",
    "balance",
    "balances",
    "transaction",
    "transactions",
    "card",
    "cards",
    "cheque",
    "cheques",
    "chequebook",
    "chequebooks",
    "checkbook",
)


def _matches(word: str, subject: str) -> bool:
    if word == subject:
        return True
    # Short card words allow swaps only, so 'car', 'cars' and 'carts' stay unrelated.
    if len(word) == len(subject):
        changed = [i for i, (a, b) in enumerate(zip(word, subject)) if a != b]
        if len(changed) == 2:
            a, b = changed
            return b == a + 1 and word[a] == subject[b] and word[b] == subject[a]
        return len(subject) >= 6 and len(changed) == 1
    if (
        min(len(word), len(subject)) < 5
        or len(subject) < 5
        or abs(len(word) - len(subject)) != 1
    ):
        return False
    shorter, longer = sorted((word, subject), key=len)
    first = next(
        (i for i, pair in enumerate(zip(shorter, longer)) if pair[0] != pair[1]),
        len(shorter),
    )
    return shorter[first:] == longer[first + 1 :]


def is_personal_request(message: str) -> bool:
    words = re.findall(r"[a-z]+", message.casefold())
    for index, word in enumerate(words):
        # Joining 'my' to a banking noun is a common typing mistake: 'myaccount balance'.
        if word.startswith("my") and word != "my":
            if any(_matches(word[2:], subject) for subject in _SUBJECTS):
                return True
        if word == "my" and any(
            _matches(candidate, subject)
            for candidate in words[index + 1 : index + 5]
            for subject in _SUBJECTS
        ):
            return True
    return False
