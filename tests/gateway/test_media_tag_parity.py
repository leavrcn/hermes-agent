from gateway.platforms.base import BasePlatformAdapter, iter_media_tag_paths
from gateway.run import _collect_tool_media_tags


class TestIterMediaTagPathsParity:
    def test_matches_extract_media_for_supported_forms(self):
        samples = [
            "MEDIA:/tmp/report.md",
            "MEDIA: /tmp/report.md",
            'MEDIA:"/tmp/report.md"',
            "MEDIA:'/tmp/report.md'",
            "MEDIA:`/tmp/my report.md`",
            'MEDIA:"/tmp/report.md",',
            "MEDIA:`/tmp/my report.md`)",
            '{"file":"MEDIA:/tmp/r.md"}',
            r"MEDIA:C:\\Users\\kotsu\\report.md",
            "MEDIA:C:/Users/kotsu/report.md",
        ]
        for sample in samples:
            media, _ = BasePlatformAdapter.extract_media(sample)
            assert iter_media_tag_paths(sample) == [path for path, _ in media], sample

    def test_rejects_unknown_extension(self):
        sample = "MEDIA:/tmp/report.xyz"
        media, _ = BasePlatformAdapter.extract_media(sample)
        assert media == []
        assert iter_media_tag_paths(sample) == []


class TestCollectToolMediaTags:
    def test_collects_quoted_and_spaced_paths(self):
        content = '\n'.join([
            'MEDIA: "/tmp/report.md"',
            "MEDIA:'/tmp/my report.md'",
            "MEDIA:`/tmp/other report.md`",
        ])
        tags, voice = _collect_tool_media_tags(content, set())
        assert tags == [
            "MEDIA:/tmp/report.md",
            "MEDIA:/tmp/my report.md",
            "MEDIA:/tmp/other report.md",
        ]
        assert voice is False

    def test_skips_history_and_preserves_voice_flag(self):
        content = "[[audio_as_voice]]\nMEDIA:'/tmp/report.md'\nMEDIA:'/tmp/fresh.md'"
        tags, voice = _collect_tool_media_tags(content, {"/tmp/report.md"})
        assert tags == ["MEDIA:/tmp/fresh.md"]
        assert voice is True
