import unittest

from vskr.access.visibility import is_visible


class TestVisibilityMatrix(unittest.TestCase):
    def test_anonymous_guest_learner_and_admin_visibility_matrix(self):
        private_by_learner = {"owner_id": "learner-a", "visibility": "private"}
        private_by_guest = {"owner_id": "guest-a", "visibility": "private"}
        public = {"owner_id": "learner-b", "visibility": "public"}

        cases = [
            ("anonymous", None, False, public, True),
            ("anonymous", None, False, private_by_learner, False),
            ("guest-own-private", "guest-a", False, private_by_guest, True),
            ("guest-other-private", "guest-a", False, private_by_learner, False),
            ("learner-own-private", "learner-a", False, private_by_learner, True),
            ("learner-other-private", "learner-a", False, private_by_guest, False),
            ("admin-public", None, True, public, True),
            ("admin-private", None, True, private_by_learner, True),
        ]
        for label, viewer_id, admin, metadata, expected in cases:
            with self.subTest(label=label):
                self.assertEqual(is_visible(metadata, viewer_id, admin=admin), expected)

    def test_shared_only_guest_scope_excludes_its_own_private_content(self):
        own_private = {"owner_id": "guest-a", "visibility": "private"}
        self.assertFalse(is_visible(own_private, "guest-a", shared_only=True))


if __name__ == "__main__":
    unittest.main()
