import uuid
from unittest.mock import MagicMock

from sygnal.notifications import Notification
from sygnal.firebasepushkin import FirebasePushkin

from tests import testutils

from firebase_admin import delete_app, exceptions as firebase_exceptions

PUSHKIN_ID = "com.example.firebase"
DEVICE_EXAMPLE = {"app_id": "com.example.firebase", "pushkey": "spqr", "pushkey_ts": 42}
FIREBASE_RETURN_VALUE = str(uuid.uuid4())


class TestFirebasePushkin(FirebasePushkin):

    def __init__(self, name, sygnal, config):
        super().__init__(name, sygnal, config)

    def _load_credentials(self):
        return None


class FirebaseTestCase(testutils.TestCase):

    def setUp(self):
        super().setUp()
        self.firebase_pushkin_notif = MagicMock()
        self.sygnal.pushkins[PUSHKIN_ID]._perform_firebase_send = self.firebase_pushkin_notif

    def tearDown(self):
        delete_app(self.sygnal.pushkins[PUSHKIN_ID]._app)
        super().tearDown()

    def _make_voip_invite_notification(self, devices, is_video=False):
        notif = self._make_dummy_notification(devices=devices)
        notif["notification"]["type"] = "m.call.invite"
        notif["notification"]["content"] = {
            "call_id": "12345",
            "lifetime": 60000,
            "offer": {
                "sdp": f"v=0\r\nm={'video' if is_video else 'audio'} 9 UDP/TLS/RTP/SAVPF\r\n",
                "type": "offer"
            },
            "version": 0
        }
        return notif

    def config_setup(self, config):
        super(FirebaseTestCase, self).config_setup(config)
        config["apps"][PUSHKIN_ID] = {
            "type": "tests.test_firebase.TestFirebasePushkin",
            "credentials": "/path/to/my/certfile.pem",
            "message_types": {
                "m.image": "<I>"
            }
        }

    def test_map_android_priority(self):
        firebase = self.sygnal.pushkins[PUSHKIN_ID]

        low = Notification({"prio": "low", "devices": []})
        high = Notification({"prio": "high", "devices": []})

        self.assertEqual(firebase._map_android_priority(low), "normal")
        self.assertEqual(firebase._map_android_priority(high), "high")

    def test_map_ios_priority(self):
        firebase = self.sygnal.pushkins[PUSHKIN_ID]

        low = Notification({"prio": "low", "devices": []})
        high = Notification({"prio": "high", "devices": []})

        self.assertEqual(firebase._map_ios_priority(low), "5")
        self.assertEqual(firebase._map_ios_priority(high), "10")

    def test_event_data_audio_call_from_notifications(self):
        """
        Test audio-call detection
        """
        payload = self._make_voip_invite_notification([DEVICE_EXAMPLE], is_video=False)["notification"]
        data = FirebasePushkin._voip_data_from_notification(Notification(payload))

        self.assertEqual(data.get("call_id"), "12345")
        self.assertEqual(data.get("is_video_call"), "false")

    def test_event_data_video_call_from_notifications(self):
        """
        Test video-call detection
        """
        payload = self._make_voip_invite_notification([DEVICE_EXAMPLE], is_video=True)["notification"]
        data = FirebasePushkin._voip_data_from_notification(Notification(payload))

        self.assertEqual(data.get("call_id"), "12345")
        self.assertEqual(data.get("is_video_call"), "true")

    def test_message_body_from_notification(self):
        firebase = self.sygnal.pushkins[PUSHKIN_ID]

        payload = self._make_dummy_notification(devices=[])["notification"]
        payload["content"] = {
            "msgtype": "m.image",
            "body": "image.jpeg"
        }
        data = firebase._message_body_from_notification(Notification(payload), firebase.config.message_types)

        self.assertEqual(data, "<I>")

    def test_firebase_expected_message(self):
        # Arrange
        method = self.firebase_pushkin_notif
        method.return_value = FIREBASE_RETURN_VALUE

        # Act
        resp = self._request(self._make_dummy_notification([DEVICE_EXAMPLE]))
        self.assertEqual(resp, {"rejected": []})

        # Assert
        self.assertEquals(1, method.call_count)
        ((notif,), _kwargs) = method.call_args

        self.assertEqual(notif.android.notification.click_action, "FIREBASE_NOTIFICATION_CLICK")
        self.assertEqual(notif.android.notification.tag, "!slw48wfj34rtnrf:example.com")
        self.assertEqual(notif.android.priority, "high")

        self.assertEqual(notif.apns.headers["apns-priority"], "10")
        self.assertEqual(notif.apns.payload.aps.badge, 2)
        self.assertEqual(notif.apns.payload.aps.thread_id, "!slw48wfj34rtnrf:example.com")

        self.assertEqual(notif.data, {
            "event_id": "$3957tyerfgewrf384",
            "room_id": "!slw48wfj34rtnrf:example.com",
            "sender_display_name": "Major Tom",
            "type": "m.room.message"
        })

        self.assertEqual(notif.notification.title, "Mission Control")
        self.assertEqual(notif.notification.body, "Major Tom: I'm floating in a most peculiar way.")

    def test_firebase_expected_voip(self):
        # Arrange
        method = self.firebase_pushkin_notif
        method.return_value = FIREBASE_RETURN_VALUE

        # Act
        resp = self._request(self._make_voip_invite_notification([DEVICE_EXAMPLE]))
        self.assertEqual(resp, {"rejected": []})

        # Assert
        self.assertEquals(1, method.call_count)
        ((notif,), _kwargs) = method.call_args

        self.assertEqual(notif.android.notification, None)
        self.assertEqual(notif.android.priority, "high")

        self.assertEqual(notif.apns.headers["apns-priority"], "10")
        self.assertEqual(notif.apns.payload.aps.badge, 2)
        self.assertEqual(notif.apns.payload.aps.thread_id, "!slw48wfj34rtnrf:example.com")

        self.assertEqual(notif.data, {
            "event_id": "$3957tyerfgewrf384",
            "room_id": "!slw48wfj34rtnrf:example.com",
            "sender_display_name": "Major Tom",
            "call_id": "12345",
            "is_video_call": "false",
            "type": "m.call.invite"
        })

        self.assertEqual(notif.notification, None)

    def test_firebase_rejection_unregistered(self):
        """
        Test that unregistered tokens
        """
        # Arrange
        method = self.firebase_pushkin_notif
        method.side_effect = firebase_exceptions.NotFoundError("Devices not registered")

        # Act
        resp = self._request(self._make_dummy_notification([DEVICE_EXAMPLE]))

        self.assertEqual(resp, {"rejected": ["spqr"]})

    def test_firebase_rejection_temporary(self):
        """
        Test that retry functionality works without succeeding
        """
        # Arrange
        method = self.firebase_pushkin_notif
        method.side_effect = firebase_exceptions.UnavailableError("Server unavailable")

        # # Act
        resp = self._request(self._make_dummy_notification([DEVICE_EXAMPLE]))
        self.assertEqual(method.call_count, 3)
        self.assertEqual(resp, 502)

    def test_firebase_rejection_temporary_success(self):
        """
        Test that retry functionality works and is able to succeed after x tries
        """
        # Arrange
        method = self.firebase_pushkin_notif
        method.side_effect = [
            firebase_exceptions.UnavailableError("Server unavailable"),
            firebase_exceptions.InternalError("Internal server error"),
            FIREBASE_RETURN_VALUE
        ]

        # # Act
        resp = self._request(self._make_dummy_notification([DEVICE_EXAMPLE]))
        self.assertEqual(method.call_count, 3)
        self.assertEqual(resp, {"rejected": []})

    def test_firebase_rejection_generic(self):
        """
        Test that unregistered tokens
        """
        # Arrange
        method = self.firebase_pushkin_notif
        method.side_effect = firebase_exceptions.PermissionDeniedError("Permission denied")

        # Act
        resp = self._request(self._make_dummy_notification([DEVICE_EXAMPLE]))

        self.assertEqual(resp, 502)

    def test_firebase_rejection_value(self):
        """
        Test that unregistered tokens
        """
        # Arrange
        method = self.firebase_pushkin_notif
        method.side_effect = ValueError("Value error")

        # Act
        resp = self._request(self._make_dummy_notification([DEVICE_EXAMPLE]))

        self.assertEqual(resp, 502)
