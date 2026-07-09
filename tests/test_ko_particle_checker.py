"""
Tests for gui/ko_particle_checker.py — Korean 조사 (particle) agreement.

No Qt dependency; pure Python.  The batchim-based check needs
``data/korean_words.txt``; the placeholder check never does.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from gui import ko_particle_checker as kpc
from gui.ko_particle_checker import (
    CONJUNCTION,
    INSTRUMENTAL,
    OBJECT,
    SUBJECT,
    TOPIC,
    check_batchim_particles,
    check_particles,
    check_placeholder_particles,
    fix_particles,
    has_batchim,
    has_rieul_batchim,
    is_hangul_syllable,
    jongseong_index,
)
from gui.quality_checker import AUTOFIX_CODES, QualityChecker


# ── Hangul syllable arithmetic ────────────────────────────────────────────────
def test_is_hangul_syllable():
    assert is_hangul_syllable("가") and is_hangul_syllable("힣")
    assert not is_hangul_syllable("A")
    assert not is_hangul_syllable("ㄱ")  # a bare jamo, not a syllable block
    assert not is_hangul_syllable("")
    assert not is_hangul_syllable("가나")


@pytest.mark.parametrize(
    "syllable,expected",
    [("가", 0), ("각", 1), ("갈", 8), ("감", 16), ("강", 21), ("삼", 16), ("투", 0), ("리", 0)],
)
def test_jongseong_index(syllable, expected):
    assert jongseong_index(syllable) == expected


def test_jongseong_index_rejects_non_hangul():
    assert jongseong_index("x") == -1


def test_has_batchim():
    assert has_batchim("람") and has_batchim("선") and has_batchim("삼")
    assert not has_batchim("다") and not has_batchim("투") and not has_batchim("리")


def test_has_rieul_batchim():
    assert has_rieul_batchim("울") and has_rieul_batchim("갈")
    assert not has_rieul_batchim("각") and not has_rieul_batchim("가")


# ── Particle pair selection ───────────────────────────────────────────────────
def test_expected_after_consonant_and_vowel():
    assert TOPIC.expected_after("람") == "은"
    assert TOPIC.expected_after("나") == "는"
    assert SUBJECT.expected_after("람") == "이"
    assert SUBJECT.expected_after("나") == "가"
    assert OBJECT.expected_after("람") == "을"
    assert OBJECT.expected_after("나") == "를"
    assert CONJUNCTION.expected_after("람") == "과"
    assert CONJUNCTION.expected_after("나") == "와"


def test_instrumental_rieul_exception():
    """A ㄹ 받침 takes 로, every other 받침 takes 으로."""
    assert INSTRUMENTAL.expected_after("울") == "로"    # 서울로
    assert INSTRUMENTAL.expected_after("각") == "으로"  # 각으로
    assert INSTRUMENTAL.expected_after("가") == "로"    # vowel-final


def test_the_fork_h3_example_is_wrong():
    """`쓰리` ends in 리 — a vowel. The fork calls it consonant-final and errs.

    Read as 에이치삼 the final 삼 does carry a ㅁ 받침, which is the only reading
    under which H3 takes 은/을/이.
    """
    assert not has_batchim("리")
    assert TOPIC.expected_after("리") == "는"
    assert has_batchim("삼")
    assert TOPIC.expected_after("삼") == "은"


# ── Placeholder check (sound, no dictionary) ──────────────────────────────────
@pytest.mark.parametrize(
    "text,expected",
    [
        ("<Alias=Player>은 죽었습니다.", "은(는)"),
        ("<Alias=Player>는 죽었습니다.", "은(는)"),
        ("%s이 도착했습니다.", "이(가)"),
        ("%s를 찾았습니다.", "을(를)"),
        ("<mag>을 회복합니다.", "을(를)"),
        ("{name}와 함께", "와(과)"),
        ("{path}로 내보냄", "(으)로"),
        ("{path}으로 내보냄", "(으)로"),
        ("<0.Name>이 필요합니다.", "이(가)"),
    ],
)
def test_placeholder_particle_flagged(text, expected):
    issues = check_placeholder_particles(text)
    assert len(issues) == 1
    assert issues[0].kind == "placeholder"
    assert issues[0].expected == expected


@pytest.mark.parametrize(
    "text",
    [
        "<Alias=Player>은(는) 죽었습니다.",   # already both-form
        "%s이(가) 도착했습니다.",
        "{path}(으)로 내보냄",
        "<Alias=X>은행에 갔다",              # 은행 = bank; 은 heads the next word
        "{count}개 항목",                    # 개 is not a particle
        "<b>사라</b>는 떠났다",               # formatting tag: the noun is known
        "우주선이 도착했습니다.",              # no placeholder at all
    ],
)
def test_placeholder_particle_not_flagged(text):
    assert check_placeholder_particles(text) == []


def test_placeholder_check_needs_no_word_list(monkeypatch):
    monkeypatch.setattr(kpc, "_is_known_word", lambda _t: None)
    assert len(check_placeholder_particles("%s를 찾았습니다.")) == 1


def test_multiple_placeholders_all_reported():
    issues = check_placeholder_particles("<Alias=A>은 %s를 봤다")
    assert [i.expected for i in issues] == ["은(는)", "을(를)"]


# ── Batchim check (precision-first) ───────────────────────────────────────────
@pytest.mark.parametrize(
    "text,context,found,expected",
    [
        ("사람는 죽었다", "사람", "는", "은"),      # ㅁ 받침 → 은
        ("함선를 수리했다", "함선", "를", "을"),    # ㄴ 받침 → 을
        ("바다은 넓다", "바다", "은", "는"),        # vowel-final → 는
    ],
)
def test_batchim_mismatch_flagged(text, context, found, expected):
    issues = check_batchim_particles(text)
    assert len(issues) == 1
    assert (issues[0].context, issues[0].found, issues[0].expected) == (context, found, expected)


@pytest.mark.parametrize(
    "text,why",
    [
        ("우주는 넓다", "우주 is vowel-final; 는 is correct"),
        ("함선을 수리했다", "함선 has a ㄴ 받침; 을 is correct"),
        ("없는 항목", "adnominal verb ending; 없는 is itself a word"),
        ("안는 자세", "안 is a one-syllable verb stem"),
        ("먹는 것", "adnominal verb ending"),
        ("가을 하늘", "가을 = autumn, a word, not 가 + 을"),
        ("모험가 등록", "가 is the Sino-Korean -家 suffix; the 이/가 pair is excluded"),
        ("반응로 동력", "로 is the Sino-Korean -爐 suffix; (으)로 is excluded"),
        ("효과 없음", "과 is the Sino-Korean -果 suffix; 과/와 is excluded"),
    ],
)
def test_batchim_not_flagged(text, why):
    assert check_batchim_particles(text) == [], why


def test_batchim_check_is_silent_without_word_list(monkeypatch):
    """No dictionary → cannot tell a particle from a verb ending → report nothing."""
    monkeypatch.setattr(kpc, "_is_known_word", lambda _t: None)
    assert check_batchim_particles("사람는 죽었다") == []


def test_batchim_excluded_pairs_are_only_topic_and_object():
    assert kpc.BATCHIM_PAIRS == [TOPIC, OBJECT]


# ── Combined + autofix ────────────────────────────────────────────────────────
def test_check_particles_orders_by_position():
    issues = check_particles("사람는 %s를 봤다")
    assert [i.kind for i in issues] == ["batchim", "placeholder"]
    assert issues[0].start < issues[1].start


def test_fix_particles_rewrites_both_kinds():
    fixed, msgs = fix_particles("사람는 %s를 봤다")
    assert fixed == "사람은 %s을(를) 봤다"
    assert msgs == ["사람는 → 사람은", "%s를 → %s을(를)"]


def test_fix_particles_is_idempotent():
    once, _ = fix_particles("<Alias=Player>은 죽었습니다.")
    assert once == "<Alias=Player>은(는) 죽었습니다."
    twice, msgs = fix_particles(once)
    assert twice == once and msgs == []


def test_fix_particles_noop_on_clean_text():
    text = "우주선이 도착했습니다."
    assert fix_particles(text) == (text, [])


# ── QualityChecker integration ────────────────────────────────────────────────
def test_particle_codes_are_autofixable():
    assert {"KO_PARTICLE_MISMATCH", "KO_PARTICLE_PLACEHOLDER"} <= AUTOFIX_CODES


def test_quality_checker_flags_korean_particles():
    qc = QualityChecker(target_language="Korean", source_language="English")
    report = qc.check(0, 1, "<Alias=Player> is dead.", "<Alias=Player>은 죽었습니다.")
    codes = {i.code for i in report.issues}
    assert "KO_PARTICLE_PLACEHOLDER" in codes


def test_quality_checker_autofixes_korean_particles():
    qc = QualityChecker(target_language="Korean", source_language="English")
    original, translated = "<Alias=Player> is dead.", "<Alias=Player>은 죽었습니다."
    report = qc.check(0, 1, original, translated)
    fixed, applied = qc.auto_fix(original, translated, report)
    assert fixed == "<Alias=Player>은(는) 죽었습니다."
    assert applied


def test_quality_checker_ignores_particles_for_other_targets():
    """The Korean word list must never be consulted for a non-Hangul target."""
    qc = QualityChecker(target_language="Ukrainian", source_language="English")
    report = qc.check(0, 1, "<Alias=Player> is dead.", "<Alias=Player>은 죽었습니다.")
    codes = {i.code for i in report.issues}
    assert "KO_PARTICLE_PLACEHOLDER" not in codes
    assert "KO_PARTICLE_MISMATCH" not in codes


# ── The same rule, taught to the model ────────────────────────────────────────
def _system_prompt(target_lang: str) -> str:
    from gui.ollama_worker import TranslationRequest

    return TranslationRequest(
        index=0, original_text="", string_id=0, source_lang="en", target_lang=target_lang
    ).to_system_prompt()


def test_korean_prompt_teaches_batchim_and_both_forms():
    prompt = _system_prompt("ko")
    assert "받침" in prompt
    assert "은(는)" in prompt          # both-form after a runtime placeholder
    assert "서울로" in prompt           # the ㄹ exception for (으)로


def test_korean_prompt_reads_acronyms_aloud():
    """O2 → 오투 (vowel-final); H3 → 에이치삼 (ㅁ 받침).  Never from Latin spelling."""
    prompt = _system_prompt("ko")
    assert "오투" in prompt and "O2는" in prompt
    assert "에이치삼" in prompt and "H3은" in prompt
    # 쓰리 is vowel-final, so it must not be used to justify a consonant particle.
    assert "쓰리" not in prompt


@pytest.mark.parametrize("lang", ["uk", "ja", "de", "pl", "en"])
def test_particle_rule_does_not_leak_into_other_targets(lang):
    prompt = _system_prompt(lang)
    assert "받침" not in prompt
    assert "은(는)" not in prompt
