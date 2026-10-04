import pytest

from finitact.automation_indicator import AutomationIndicator


class FakeOverlay:
    def __init__(self, *, class_name):
        self.class_name = class_name
        self.started = False
        self.closed = False
        self.calls = []

    def start(self):
        self.started = True

    def show(self, *, x, y, image):
        self.calls.append(("show", x, y, image))

    def update(self, *, image):
        self.calls.append(("update", image))

    def hide(self):
        self.calls.append(("hide",))

    def close(self):
        self.closed = True


class FakeOverlayFactory:
    def __init__(self):
        self.instances = []

    def __call__(self, *, class_name):
        overlay = FakeOverlay(class_name=class_name)
        self.instances.append(overlay)
        return overlay


def indicator(**kwargs):
    kwargs.setdefault("overlay_factory", FakeOverlayFactory())
    kwargs.setdefault("screen_size", lambda: (1920, 1080))
    kwargs.setdefault("window_origin", lambda hwnd: (100, 200))
    return AutomationIndicator(**kwargs)


def test_show_starts_and_positions_the_badge_overlay():
    factory = FakeOverlayFactory()
    ind = indicator(overlay_factory=factory, margin=10)
    ind.show()
    badge = factory.instances[0]
    assert badge.class_name == "FinitactIndicatorBadge"
    assert badge.started
    kind, x, y, image = badge.calls[0]
    assert kind == "show"
    assert (x, y) == (1920 - image.width - 10, 1080 - image.height - 10)


def test_show_refuses_a_second_badge():
    ind = indicator()
    ind.show()
    with pytest.raises(RuntimeError):
        ind.show()


def test_close_before_show_is_a_no_op():
    indicator().close()  # must not raise


def test_close_tears_down_badge_and_reticle_overlays():
    factory = FakeOverlayFactory()
    ind = indicator(overlay_factory=factory)
    ind.show()
    with ind.reticle(hwnd=1, rect=(0, 0, 10, 10)):
        pass
    ind.close()
    assert all(overlay.closed for overlay in factory.instances)


def test_context_manager_shows_then_closes():
    factory = FakeOverlayFactory()
    with indicator(overlay_factory=factory):
        assert factory.instances[0].started
    assert factory.instances[0].closed


def test_delivering_requires_the_badge_to_already_be_shown():
    ind = indicator()
    with pytest.raises(RuntimeError):
        with ind.delivering():
            pass


def test_delivering_brightens_then_restores_the_badge():
    factory = FakeOverlayFactory()
    ind = indicator(overlay_factory=factory)
    ind.show()
    badge = factory.instances[0]
    with ind.delivering():
        assert badge.calls[-1][0] == "update"
        delivering_image = badge.calls[-1][1]
    idle_image = badge.calls[-1][1]
    assert delivering_image.bgra_premultiplied != idle_image.bgra_premultiplied


def test_reticle_creates_one_overlay_and_reuses_it_across_deliveries():
    factory = FakeOverlayFactory()
    ind = indicator(overlay_factory=factory)
    with ind.reticle(hwnd=1, rect=(0, 0, 10, 10)):
        pass
    with ind.reticle(hwnd=1, rect=(0, 0, 10, 10)):
        pass
    reticle_overlays = [o for o in factory.instances if o.class_name == "FinitactIndicatorReticle"]
    assert len(reticle_overlays) == 1


def test_reticle_positions_relative_to_the_window_origin_and_hides_on_exit():
    factory = FakeOverlayFactory()
    ind = indicator(overlay_factory=factory, window_origin=lambda hwnd: (500, 300))
    with ind.reticle(hwnd=1, rect=(20, 30, 10, 10)):
        pass
    overlay = factory.instances[0]
    first_show = next(call for call in overlay.calls if call[0] == "show")
    _, x, y, _ = first_show
    assert x < 500 + 20  # canvas starts left of the target rect (corner offset + padding)
    assert y < 300 + 30
    assert overlay.calls[-1] == ("hide",)


def test_reticle_readiness_failure_propagates_and_never_yields():
    class FailingFactory:
        def __call__(self, *, class_name):
            raise RuntimeError("overlay window failed to start")

    ind = indicator(overlay_factory=FailingFactory())
    with pytest.raises(RuntimeError, match="failed to start"):
        with ind.reticle(hwnd=1, rect=(0, 0, 10, 10)):
            pytest.fail("delivery must not start")
