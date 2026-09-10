# `M` helper quick reference

`M` is available only inside the existing `apply` tool. It is not a fourth MCP tool.

```python
M.material(name, color, roughness=.7, metallic=0)
M.mesh(name, vertices, faces, collection=None, material=None, smooth=True)
M.tube(name, points, radius, collection=None, material=None, sides=8, smooth=True)
M.clump(name, profiles, collection=None, material=None, sides=8, smooth=True)
M.panel(name, points, depth, collection=None, material=None, axis="y", smooth=False)
M.ellipsoid(name, center, radii, collection=None, material=None, segments=16, rings=8, smooth=True)
```

`M.clump` profiles use `[x, y, z, width, depth]`. `M.tube` accepts one radius or a radius per path point. Reusing an existing mesh object name replaces its mesh data while retaining the object identity/transform and existing collection membership.

The intended pattern is a short batch such as:

```python
M.clump("Speaky_Hair_Bang_01", [
    (-.04,-.08,1.66,.024,.012),
    (-.05,-.09,1.61,.020,.010),
    (-.06,-.10,1.57,.002,.002),
], "Speaky_Hair", "Speaky_Hair_Ivory")
```

Prefer several small named edits over redefining general helper functions in generated code.
