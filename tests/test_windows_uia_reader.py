

def test_walk_once_stops_at_elements_already_seen_in_a_cyclic_tree():
    from types import SimpleNamespace

    from finitact.windows_uia_reader import _walk_once

    class Array:
        def __init__(self, items):
            self.items, self.Length = items, len(items)

        def GetElement(self, index):
            return self.items[index]

    class Element:
        def __init__(self, runtime_id):
            self.runtime_id, self.children, self.native = runtime_id, [], 0

        def FindAllBuildCache(self, scope, condition, request):
            return Array(self.children)

        def GetCachedPropertyValue(self, prop):
            return self.runtime_id if prop == 30000 else self.native

    # E2E-I38: a pane below the window lists the window's top-level panes again as its children.
    root, top, frame, pane = Element((0,)), Element((1,)), Element((2,)), Element((3,))
    root.children = [top, frame]
    frame.children = [pane]
    pane.children = [top, frame]
    popup = Element((4,))
    popup.native, popup.children = 77, [Element((5,))]
    pane.children.append(popup)
    fake = SimpleNamespace(TreeScope_Children=2, UIA_RuntimeIdPropertyId=30000, UIA_NativeWindowHandlePropertyId=30020)
    walked = _walk_once(fake, root, None, None, skip_windows=frozenset({77}))
    # The owned popup (hwnd 77) is its own frame; through the owner a click on it was refused as covered.
    assert [element.runtime_id for element in walked] == [(1,), (2,), (3,)]
