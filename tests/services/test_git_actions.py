from pathlib import Path

from tests.services.support import ServiceCase, col, wp
from wallman.domain.models import Ref
from wallman.errors import NotShareable, WallmanError
from wallman.services.git_actions import GitActions
from wallman.services.remover import Remover


class GitActionsTest(ServiceCase):
    def actions(self):
        return GitActions(self.cat, self.store, self.vcs, self.uow)

    def p(self, folder, file):
        return self.store.path(folder, file)

    def test_git_add_stages_shareable_without_force(self):
        self.build([col(wallpapers=[wp("a.jpg"), wp("b.jpg")])], {"painting/x": {"a.jpg", "b.jpg"}})
        out = self.actions().add([Ref("painting/x", "a.jpg"), Ref("painting/x", "b.jpg")])
        self.assertEqual(len(out), 2)
        self.assertEqual(self.vcs.calls, [("stage_add", [self.p("painting/x", "a.jpg"), self.p("painting/x", "b.jpg")])])

    def test_git_writes_are_serialised_with_other_writes(self):
        """add/remove run under the same lock as UnitOfWork writes: two concurrent calls never overlap in git."""
        import threading, time
        self.build([col(wallpapers=[wp("a.jpg"), wp("b.jpg")])], {"painting/x": {"a.jpg", "b.jpg"}},
                   tracked=[("painting/x", "b.jpg")])
        active, most = [0], [0]
        guard = threading.Lock()

        def slow(real):
            def run(*a, **kw):
                with guard:
                    active[0] += 1
                    most[0] = max(most[0], active[0])
                time.sleep(0.05)
                with guard:
                    active[0] -= 1
                return real(*a, **kw)
            return run

        self.vcs.stage_add = slow(self.vcs.stage_add)
        self.vcs.untrack = slow(self.vcs.untrack)
        acts = self.actions()
        errors = []

        def call(fn):
            try:
                fn()
            except Exception as e:  # pragma: no cover - reported below
                errors.append(e)

        def uow_write():
            with self.uow():
                with guard:
                    active[0] += 1
                    most[0] = max(most[0], active[0])
                time.sleep(0.05)
                with guard:
                    active[0] -= 1

        threads = [threading.Thread(target=call, args=(f,)) for f in (
            lambda: acts.add([Ref("painting/x", "a.jpg")]), lambda: acts.remove([Ref("painting/x", "b.jpg")]), uow_write)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(most[0], 1)

    def test_git_add_refuses_non_shareable(self):
        self.build([col(wallpapers=[wp("a.jpg"), wp("b.jpg", license="all-rights-reserved")])],
                   {"painting/x": {"a.jpg", "b.jpg"}})
        with self.assertRaises(NotShareable) as cm:
            self.actions().add([Ref("painting/x", "a.jpg"), Ref("painting/x", "b.jpg")])
        self.assertIn("painting/x/b.jpg", str(cm.exception))
        self.assertNotIn("painting/x/a.jpg", str(cm.exception))
        self.assertEqual(self.vcs.calls, [])  # nothing staged, not even the shareable one
        self.assertEqual(NotShareable.http_status, 422)

    def test_remove_keeps_file(self):
        self.build([col(wallpapers=[wp("a.jpg"), wp("b.jpg")])], {"painting/x": {"a.jpg", "b.jpg"}},
                   tracked=[("painting/x", "a.jpg")])
        out = self.actions().remove([Ref("painting/x", "a.jpg"), Ref("painting/x", "b.jpg")])
        self.assertEqual(out, [Ref("painting/x", "a.jpg")])  # untracked b is a no-op
        self.assertEqual(self.vcs.calls, [("untrack", [self.p("painting/x", "a.jpg")])])
        self.assertTrue(self.p("painting/x", "a.jpg").exists())
        self.assertEqual([c for c in self.store.calls if c[0] == "delete"], [])

    def test_remove_untracked_only_does_nothing(self):
        self.build([col(wallpapers=[wp("b.jpg")])], {"painting/x": {"b.jpg"}})
        self.assertEqual(self.actions().remove([Ref("painting/x", "b.jpg")]), [])
        self.assertEqual(self.vcs.calls, [])

    def test_external_allowed_alone_but_not_mixed(self):
        self.build([col("ml4w", [wp("a.jpg")], "ml4w"), col("painting/x", [wp("b.jpg")])],
                   {"ml4w": {"a.jpg"}, "painting/x": {"b.jpg"}}, external={"ml4w"})
        self.actions().add([Ref("ml4w", "a.jpg")])
        self.assertEqual(self.vcs.calls, [("stage_add", [self.p("ml4w", "a.jpg")])])
        for method in ("add", "remove"):
            with self.assertRaises(WallmanError) as cm:
                getattr(self.actions(), method)([Ref("ml4w", "a.jpg"), Ref("painting/x", "b.jpg")])
            self.assertIn("mixing submodule and main-repo files", str(cm.exception))
        self.assertEqual(len(self.vcs.calls), 1)

    def test_two_external_collections_refused(self):
        self.build([col("ml4w", [wp("a.jpg")], "ml4w"), col("ml4w2", [wp("b.jpg")], "ml4w2")],
                   {"ml4w": {"a.jpg"}, "ml4w2": {"b.jpg"}}, external={"ml4w", "ml4w2"})
        with self.assertRaises(WallmanError):
            self.actions().add([Ref("ml4w", "a.jpg"), Ref("ml4w2", "b.jpg")])

    def test_delete_plan_splits_tracked_untracked_external(self):
        self.build([col(wallpapers=[wp(n) for n in "abcdefg"]), col("ml4w", [wp("m.jpg")], "ml4w")],
                   {"painting/x": {n + ".jpg" for n in "abcdefg"}, "ml4w": {"m.jpg"}}, external={"ml4w"},
                   tracked=[("painting/x", "a.jpg"), ("ml4w", "m.jpg")])
        self.vcs.states = {self.p("painting/x", "b.jpg"): "staged", self.p("painting/x", "c.jpg"): "modified",
                           self.p("painting/x", "d.jpg"): "deleted", self.p("painting/x", "e.jpg"): "ignored",
                           self.p("painting/x", "f.jpg"): "untracked", self.p("painting/x", "g.jpg"): "conflict", self.p("ml4w", "m.jpg"): "committed"}
        refs = [Ref("painting/x", n + ".jpg") for n in "abcdefg"] + [Ref("ml4w", "m.jpg")]
        plan = self.actions().delete_plan(refs)
        self.assertEqual([r.file for r in plan.tracked], ["a.jpg", "b.jpg", "c.jpg", "d.jpg", "g.jpg"])
        self.assertEqual([r.file for r in plan.untracked], ["e.jpg", "f.jpg"])
        self.assertEqual(plan.external, [Ref("ml4w", "m.jpg")])


class RemoverKeepTest(ServiceCase):
    def test_delete_keep_leaves_index(self):
        self.build([col(wallpapers=[wp("a.jpg"), wp("b.jpg")])], {"painting/x": {"a.jpg", "b.jpg"}},
                   tracked=[("painting/x", "a.jpg")])
        a = self.store.path("painting/x", "a.jpg")
        n = Remover(self.cat, self.store, self.vcs, self.uow).delete([Ref("painting/x", "a.jpg")], git="keep")
        self.assertEqual(n, 1)
        self.assertEqual([c for c in self.vcs.calls if c[0] in ("stage_rm", "untrack")], [])
        self.assertIn(("delete", a), self.store.calls)
        self.assertFalse(a.exists())
        self.assertIn(a, self.vcs.tracked)
        self.assertEqual([w.file for w in self.cat.load("painting/x").wallpapers], ["b.jpg"])
        self.assertIn(("stage_add", [self.toml("painting/x")]), self.vcs.calls)

    def test_delete_rejects_unknown_git_mode(self):
        self.build([col(wallpapers=[wp("a.jpg")])], {"painting/x": {"a.jpg"}})
        with self.assertRaises(ValueError):
            Remover(self.cat, self.store, self.vcs, self.uow).delete([Ref("painting/x", "a.jpg")], git="x")
