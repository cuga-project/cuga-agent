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
