from datetime import UTC, datetime
from unittest import TestCase

from audiodigest.config import load_settings
from audiodigest.constants import Section
from audiodigest.jobs import (
    JobValidationError,
    ScheduledJob,
    apply_generation_parameters,
)


def schedule_payload() -> dict:
    return {
        "scheduleId": "executive-morning",
        "name": "Executive morning",
        "enabled": True,
        "timezone": "UTC",
        "startTime": "04:45",
        "readyBy": "06:00",
        "weekdays": [0, 1, 2, 3, 4],
        "parameters": {
            "runName": "Executive morning",
            "gmailLabel": "AudioDigest/Source",
            "sections": ["AI Strategy", "Markets and Policy"],
            "hostCount": 2,
            "soloName": "Dalia",
            "dialogueStyle": "conversation",
            "primaryVoice": "af_heart",
            "primaryTone": "warm",
            "secondaryVoice": "am_michael",
            "secondaryTone": "dry_wit",
            "publish": True,
            "dateMode": "previous_day",
        },
    }


class ScheduledJobTests(TestCase):
    def test_schedule_parameters_are_normalized_and_applied(self):
        job = ScheduledJob.from_dict(schedule_payload())
        self.assertEqual(
            (
                Section.TODAY_IN_HISTORY.value,
                "AI Strategy",
                "Markets and Policy",
            ),
            job.parameters.sections,
        )
        settings = apply_generation_parameters(
            load_settings("config.example.toml"),
            job.parameters,
        )
        self.assertEqual(2, settings.hosts.count)
        self.assertEqual("conversation", settings.hosts.dialogue_style)
        self.assertEqual("automatic", settings.firebase.publish_mode)
        self.assertEqual(job.parameters.sections, settings.podcast.sections)
        self.assertEqual("standard", settings.podcast.newspaper_edition_scale)

    def test_schedule_is_due_in_its_own_timezone(self):
        job = ScheduledJob.from_dict(schedule_payload())
        self.assertTrue(job.is_due(datetime(2026, 7, 27, 4, 47, tzinfo=UTC)))
        self.assertTrue(job.is_due(datetime(2026, 7, 27, 8, 1, tzinfo=UTC)))
        self.assertFalse(job.is_due(datetime(2026, 7, 27, 1, 1, tzinfo=UTC)))
        self.assertEqual(
            "2026-07-26",
            job.episode_date(datetime(2026, 7, 27, 4, 47, tzinfo=UTC)).isoformat(),
        )

    def test_blank_sections_enable_auto_assignment(self):
        payload = schedule_payload()
        payload["parameters"]["sections"] = []
        job = ScheduledJob.from_dict(payload)
        self.assertEqual((), job.parameters.sections)

    def test_section_limit_includes_automatic_tih_section(self):
        payload = schedule_payload()
        payload["parameters"]["sections"] = [
            f"Section {index}" for index in range(10)
        ]
        self.assertEqual(
            11,
            len(ScheduledJob.from_dict(payload).parameters.sections),
        )
        payload["parameters"]["sections"].append("Section 10")
        with self.assertRaisesRegex(JobValidationError, "no more than 10"):
            ScheduledJob.from_dict(payload)

    def test_tih_can_be_turned_off_without_using_a_section_slot(self):
        payload = schedule_payload()
        payload["parameters"]["includeTih"] = False
        job = ScheduledJob.from_dict(payload)
        self.assertNotIn(Section.TODAY_IN_HISTORY.value, job.parameters.sections)

    def test_edition_scale_accepts_focused_and_rejects_unknown_values(self):
        payload = schedule_payload()
        payload["parameters"]["editionScale"] = "focused"
        self.assertEqual(
            "focused",
            ScheduledJob.from_dict(payload).parameters.newspaper_edition_scale,
        )

        payload["parameters"]["editionScale"] = "oversized"
        with self.assertRaisesRegex(JobValidationError, "editionScale"):
            ScheduledJob.from_dict(payload)

    def test_newsletter_only_mode_requires_tih_to_be_off(self):
        payload = schedule_payload()
        payload["parameters"].update(
            {"evidenceMode": "newsletter_only", "includeTih": False}
        )
        self.assertEqual(
            "newsletter_only",
            ScheduledJob.from_dict(payload).parameters.evidence_mode,
        )

        payload["parameters"]["includeTih"] = True
        with self.assertRaisesRegex(JobValidationError, "TIH"):
            ScheduledJob.from_dict(payload)

    def test_invalid_schedule_id_is_rejected(self):
        payload = schedule_payload()
        payload["scheduleId"] = "../secret"
        with self.assertRaises(JobValidationError):
            ScheduledJob.from_dict(payload)

    def test_host_voice_assignments_are_distinct_and_host_appropriate(self):
        payload = schedule_payload()
        payload["parameters"]["primaryVoice"] = "am_eric"
        self.assertEqual("bf_emma", ScheduledJob.from_dict(payload).parameters.primary_voice)

        payload = schedule_payload()
        payload["parameters"]["secondaryVoice"] = "af_bella"
        self.assertEqual("am_puck", ScheduledJob.from_dict(payload).parameters.secondary_voice)

        payload = schedule_payload()
        payload["parameters"].update(
            {"hostCount": 1, "soloName": "Nox", "primaryVoice": "am_puck"}
        )
        job = ScheduledJob.from_dict(payload)
        settings = apply_generation_parameters(load_settings("config.example.toml"), job.parameters)
        self.assertEqual("am_puck", settings.hosts.secondary_voice)
