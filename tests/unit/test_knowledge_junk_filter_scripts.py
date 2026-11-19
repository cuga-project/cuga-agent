"""The junk filter keeps short real sentences in every script and still drops caption fragments."""

import pytest

from cuga.backend.knowledge.engine import _classify_junk_chunk

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "text",
    [
        "我们的数据备份策略每天执行一次。",  # 16 characters, a full Chinese sentence
        "毎晩コーヒーを飲みながらバックアップを確認します。",
        "서울에서 백업 정책을 검토했습니다.",
        "นโยบายการสำรองข้อมูลของบริษัทกำหนดให้สำรองทุกวัน",
        "हिन्दी भाषा में यह दस्तावेज़ बैकअप नीति का वर्णन करता है।",
        "தமிழ் மொழியில் இந்த ஆவணம் காப்புப் பிரதி கொள்கையை விவரிக்கிறது.",
        "שָׁלוֹם לְכֻלָּם, הַגִּבּוּי מִתְבַּצֵּעַ כָּל לַיְלָה.",
        "تُحْفَظُ النُّسْخَةُ الاحْتِيَاطِيَّةُ كُلَّ لَيْلَةٍ فِي مَرْكَزَيْنِ.",
    ],
    ids=["zh", "ja", "ko", "th", "hi", "ta", "he", "ar"],
)
def test_short_sentence_is_kept(text):
    assert _classify_junk_chunk(text, "policy.pdf") is None


@pytest.mark.parametrize("text", ["Figure 3: backup", "第三章 备份策略"], ids=["en-caption", "zh-heading"])
def test_caption_fragment_is_dropped(text):
    assert _classify_junk_chunk(text, "policy.pdf") == "too_short"


@pytest.mark.parametrize(
    ("text", "columns"),
    [
        ("Backups run every night at 2.", 29),
        ("Backups run every night at 2am", 30),
        ("备份策略每天执行一次检查结果", 28),  # 14 wide characters
        ("备份策略每天执行一次检查结果。", 30),  # 15 wide characters
        ("备份策略每天执行一次检查 2am.", 29),  # 12 wide + 5 narrow
        ("备份策略每天执行一次检查 2 am.", 30),  # 12 wide + 6 narrow
    ],
    ids=["en-29", "en-30", "zh-28", "zh-30", "mixed-29", "mixed-30"],
)
def test_too_short_means_under_30_columns(text, columns):
    """A wide character counts two columns, every other character one, as len() did before, so
    the rule keeps every chunk it kept before and drops fewer wide-script ones."""
    assert (_classify_junk_chunk(text, "policy.pdf") == "too_short") is (columns < 30)


@pytest.mark.parametrize("form", ["NFC", "NFD"])
def test_a_decomposed_heading_is_measured_like_the_composed_one(form):
    """NFD splits voiced kana and Hangul syllables into parts; each part would count a column."""
    import unicodedata

    for heading in ("データバックアップガイド", "백업 정책 안내서"):  # 24 and 15 columns composed
        assert _classify_junk_chunk(unicodedata.normalize(form, heading), "policy.pdf") == "too_short"
