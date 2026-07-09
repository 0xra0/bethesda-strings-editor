"""
Korean particle (조사) agreement checker — deterministic, no morphological library.

Korean particle allomorphs are selected by the **받침** (final consonant) of the
syllable they attach to::

    받침 있음 (consonant-final)      받침 없음 (vowel-final)
    사람은  사람이  사람을           나는  내가  나를

Hangul syllables decompose arithmetically — ``(ord(ch) - 0xAC00) % 28`` is the
final-consonant index, ``0`` meaning none — so the correct particle for a given
stem is *computed*, never guessed.  Two checks are exposed.

``check_placeholder_particles`` — sound, no dictionary
    A particle written in a single form directly after a **value placeholder**
    (``<Alias=Player>``, ``%s``, ``{name}``, ``<mag>``…) is always a latent bug:
    the noun substituted at run time is unknown, and Bethesda's engine performs
    no particle resolution.  Korean localization convention is to emit both
    forms — ``<Alias=Player>은(는)``.  Applies to every pair.

``check_batchim_particles`` — sound only for a deliberately narrow subset
    Verifying a particle against a plain Hangul stem needs to know the stem is a
    *noun*, and there is no POS tagger here.  Three homograph classes make a
    naive check actively harmful, because these issues are auto-fixed unattended:

      * ``가``, ``과``, ``로`` are productive Sino-Korean noun-forming suffixes.
        ``모험가`` (adventurer), ``효과`` (effect) and ``반응로`` (reactor) are
        single nouns, not stem+particle, and no word list covers them all — so
        the 이/가, 과/와 and (으)로 pairs are excluded from this check entirely.
      * ``-는`` is also the adnominal verb ending (``먹는 것``).  Verb stems are
        overwhelmingly one syllable or vowel-final, so a two-syllable
        consonant-final stem is required before the topic particle is judged.
      * A token that is itself a dictionary word (``없는``, ``가을``) is skipped.

    What remains — 은/는 and 을/를 on a known multi-syllable stem — has no
    homograph, so a reported mismatch is a real mismatch.  Recall is traded away
    for precision, the same bargain ``gender_checker`` makes for Ukrainian.

Pure Python, no Qt.  The word list is ``data/korean_words.txt`` via
``gui.ko_word_checker``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

# ── Hangul syllable arithmetic ────────────────────────────────────────────────
_HANGUL_FIRST = 0xAC00          # 가
_HANGUL_LAST = 0xD7A3           # 힣
_JONGSEONG_COUNT = 28           # 0 = no final consonant, 1..27 = ㄱ..ㅎ
_JONGSEONG_RIEUL = 8            # ㄹ


def is_hangul_syllable(ch: str) -> bool:
    """True if *ch* is a precomposed Hangul syllable block (U+AC00–U+D7A3)."""
    return len(ch) == 1 and _HANGUL_FIRST <= ord(ch) <= _HANGUL_LAST


def jongseong_index(ch: str) -> int:
    """Final-consonant index of *ch*: 0 = none, 1–27 = ㄱ…ㅎ.  -1 if not Hangul."""
    if not is_hangul_syllable(ch):
        return -1
    return (ord(ch) - _HANGUL_FIRST) % _JONGSEONG_COUNT


def has_batchim(ch: str) -> bool:
    """True if the Hangul syllable *ch* ends in a consonant."""
    return jongseong_index(ch) > 0


def has_rieul_batchim(ch: str) -> bool:
    """True if the Hangul syllable *ch* ends in ㄹ — the (으)로 exception."""
    return jongseong_index(ch) == _JONGSEONG_RIEUL


# ── Particle pairs ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ParticlePair:
    """One 조사 allomorph pair and the both-form written after a placeholder."""

    consonant: str      # form after a 받침
    vowel: str          # form after a vowel
    both_form: str      # convention when the preceding noun is unknown
    role: str           # human label

    # (으)로 is the odd one out: a ㄹ 받침 takes the vowel form 로, not 으로.
    rieul_takes_vowel_form: bool = False

    def expected_after(self, syllable: str) -> str:
        """The correct allomorph when this pair attaches to *syllable*."""
        if not has_batchim(syllable):
            return self.vowel
        if self.rieul_takes_vowel_form and has_rieul_batchim(syllable):
            return self.vowel
        return self.consonant


TOPIC = ParticlePair("은", "는", "은(는)", "topic")
SUBJECT = ParticlePair("이", "가", "이(가)", "subject")
OBJECT = ParticlePair("을", "를", "을(를)", "object")
# The written convention lists the vowel form first for this pair.
CONJUNCTION = ParticlePair("과", "와", "와(과)", "conjunction")
INSTRUMENTAL = ParticlePair("으로", "로", "(으)로", "instrumental", rieul_takes_vowel_form=True)

PARTICLE_PAIRS: List[ParticlePair] = [TOPIC, SUBJECT, OBJECT, CONJUNCTION, INSTRUMENTAL]

# 가 / 과 / 로 double as Sino-Korean noun suffixes (모험가, 효과, 반응로), which no
# word list enumerates — judging them against a bare Hangul stem produces false
# positives that the auto-fixer would then bake into the translation.
BATCHIM_PAIRS: List[ParticlePair] = [TOPIC, OBJECT]

# "-는" is the adnominal verb ending too (먹는, 안는).  Verb stems are almost
# always one syllable or vowel-final, so demand a longer consonant-final stem.
_MIN_BATCHIM_STEM_SYLLABLES = 2


def _forms_longest_first(pairs: List[ParticlePair]) -> List[Tuple[str, ParticlePair]]:
    """Surface forms of *pairs*, longest first so "으로" wins over "로"."""
    forms = [(p.consonant, p) for p in pairs] + [(p.vowel, p) for p in pairs]
    return sorted(forms, key=lambda item: -len(item[0]))


_ALL_FORMS = _forms_longest_first(PARTICLE_PAIRS)
_BATCHIM_FORMS = _forms_longest_first(BATCHIM_PAIRS)

# Every accepted both-form spelling, so an already-correct string is left alone.
_BOTH_FORMS: List[str] = [p.both_form for p in PARTICLE_PAIRS] + [
    "는(은)", "가(이)", "를(을)", "과(와)", "로(으로)",
]

# ── Value placeholders ────────────────────────────────────────────────────────
# Only tokens whose *value* is substituted at run time.  Formatting tags (<b>,
# <br>, </font>) wrap known text, so a particle after them is verifiable and is
# deliberately not matched here.
_PLACEHOLDER_RE = re.compile(
    r"<(?:Alias|TokenAlias|Token|Global)(?:[.=][^>]*)?>"
    r"|<CurrentName>"
    r"|<\d+\.[A-Za-z]+>"
    r"|<(?:mag|dur|area|repetitions)>"
    r"|<(?:relat|basename)[^>]*>"
    r"|%[-+#0]*(?:\*|\d+)?(?:\.(?:\*|\d+))?[diouxXeEfFgGcsSp]"
    r"|\{[^}]+\}",
    re.IGNORECASE,
)

_HANGUL_RUN_RE = re.compile(r"[가-힣]+")


@dataclass
class ParticleIssue:
    """One particle defect, with the exact replacement that repairs it."""

    kind: str           # "placeholder" | "batchim"
    start: int          # index of the particle within the checked text
    end: int
    found: str          # particle as written
    expected: str       # particle as it should be written
    context: str        # the placeholder or stem the particle attaches to
    role: str           # "topic", "object", …

    @property
    def message(self) -> str:
        if self.kind == "placeholder":
            return (
                f"Korean {self.role} particle “{self.found}” follows the runtime "
                f"placeholder “{self.context}” — the substituted noun's 받침 is "
                f"unknown, so write “{self.expected}”"
            )
        return (
            f"Korean {self.role} particle “{self.found}” disagrees with the 받침 "
            f"of “{self.context}” — expected “{self.expected}”"
        )


def _match_both_form(text: str) -> Optional[str]:
    """Return the both-form spelling *text* starts with, if any."""
    for form in _BOTH_FORMS:
        if text.startswith(form):
            return form
    return None


def _match_particle(text: str, forms: List[Tuple[str, ParticlePair]]) -> Optional[Tuple[str, ParticlePair]]:
    """Return (surface_form, pair) if *text* starts with one of *forms*."""
    for form, pair in forms:
        if text.startswith(form):
            return form, pair
    return None


def _match_particle_suffix(
    token: str, forms: List[Tuple[str, ParticlePair]]
) -> Optional[Tuple[str, str, ParticlePair]]:
    """Split *token* into (stem, particle_form, pair), longest particle first."""
    for form, pair in forms:
        if len(token) > len(form) and token.endswith(form):
            return token[: -len(form)], form, pair
    return None


def check_placeholder_particles(text: str) -> List[ParticleIssue]:
    """Single-form particles attached to a runtime placeholder.

    Exact: no dictionary, no heuristics.  A particle is only recognised when a
    non-Hangul boundary follows it, so ``<Alias=X>은행`` ("X Bank") is untouched.
    """
    issues: List[ParticleIssue] = []
    for m in _PLACEHOLDER_RE.finditer(text):
        tail = text[m.end():]
        if not tail or _match_both_form(tail):
            continue
        hit = _match_particle(tail, _ALL_FORMS)
        if hit is None:
            continue
        form, pair = hit
        after = tail[len(form):]
        if after and is_hangul_syllable(after[0]):
            continue  # the particle syllable really heads the next word
        issues.append(
            ParticleIssue(
                kind="placeholder",
                start=m.end(),
                end=m.end() + len(form),
                found=form,
                expected=pair.both_form,
                context=m.group(0),
                role=pair.role,
            )
        )
    return issues


def _is_known_word(token: str) -> Optional[bool]:
    """True/False if *token* is/isn't in the Korean word list; None if unavailable."""
    try:
        from gui.ko_word_checker import word_is_korean

        return word_is_korean(token)
    except Exception:  # pragma: no cover - word list is optional
        return None


def check_batchim_particles(text: str) -> List[ParticleIssue]:
    """Particles whose allomorph disagrees with the stem's 받침.

    Returns nothing when the word list is unavailable — without it a particle
    cannot be told apart from a homographic verb ending, and a checker that
    guesses is worse than none, because the auto-fixer acts on what it reports.
    """
    issues: List[ParticleIssue] = []
    for run in _HANGUL_RUN_RE.finditer(text):
        token = run.group(0)
        if _is_known_word(token) is not False:
            # In the dictionary ("없는", "가을") → a word, not stem+particle.
            # None → the list is missing; refuse to guess.
            continue
        hit = _match_particle_suffix(token, _BATCHIM_FORMS)
        if hit is None:
            continue
        stem, form, pair = hit
        if len(stem) < _MIN_BATCHIM_STEM_SYLLABLES:
            continue  # too short to rule out a verb stem ("안" + 는)
        if _is_known_word(stem) is not True:
            continue  # can't prove this is a noun + particle
        expected = pair.expected_after(stem[-1])
        if form == expected:
            continue
        start = run.start() + len(stem)
        issues.append(
            ParticleIssue(
                kind="batchim",
                start=start,
                end=start + len(form),
                found=form,
                expected=expected,
                context=stem,
                role=pair.role,
            )
        )
    return issues


def check_particles(text: str) -> List[ParticleIssue]:
    """All particle issues in *text*, ordered by position."""
    issues = check_placeholder_particles(text) + check_batchim_particles(text)
    issues.sort(key=lambda i: i.start)
    return issues


def fix_particles(text: str) -> Tuple[str, List[str]]:
    """Rewrite every detected particle to its correct form.

    Returns ``(fixed_text, [description, …])``.  Applied right-to-left so earlier
    offsets stay valid as the string changes length.
    """
    issues = check_particles(text)
    if not issues:
        return text, []

    descriptions: List[str] = []
    for issue in sorted(issues, key=lambda i: i.start, reverse=True):
        text = text[: issue.start] + issue.expected + text[issue.end:]
        descriptions.append(
            f"{issue.context}{issue.found} → {issue.context}{issue.expected}"
        )
    descriptions.reverse()
    return text, descriptions
