from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import bpy
from mathutils import Vector

MAX_VERTICES_PER_OBJECT = 100_000
MAX_FACES_PER_OBJECT = 100_000
MAX_PATH_POINTS = 512
MAX_SIDES = 48


def _collection(name: str | None) -> bpy.types.Collection:
    if not name:
        return bpy.context.scene.collection
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(collection)
    return collection


def _material(name: str | None) -> bpy.types.Material | None:
    if not name:
        return None
    material = bpy.data.materials.get(name)
    if material is None:
        raise KeyError(f"material_not_found:{name}")
    return material


def _validate_geometry(vertices: Sequence[Any], faces: Sequence[Any]) -> None:
    if len(vertices) > MAX_VERTICES_PER_OBJECT:
        raise ValueError(f"helper_vertices_too_large:{len(vertices)}")
    if len(faces) > MAX_FACES_PER_OBJECT:
        raise ValueError(f"helper_faces_too_large:{len(faces)}")


def _frame(points: Sequence[Vector], index: int) -> tuple[Vector, Vector]:
    before = points[max(0, index - 1)]
    after = points[min(len(points) - 1, index + 1)]
    tangent = after - before
    if tangent.length_squared < 1e-12:
        tangent = Vector((0.0, 0.0, 1.0))
    else:
        tangent.normalize()

    preferred = Vector((1.0, 0.0, 0.0))
    if abs(tangent.dot(preferred)) > 0.94:
        preferred = Vector((0.0, 1.0, 0.0))
    u = preferred - tangent * tangent.dot(preferred)
    if u.length_squared < 1e-12:
        u = Vector((0.0, 1.0, 0.0))
    u.normalize()
    v = tangent.cross(u)
    if v.length_squared < 1e-12:
        v = Vector((0.0, 0.0, 1.0))
    else:
        v.normalize()
    return u, v


class ModelingHelpers:
    """Small reusable geometry vocabulary exposed to apply() as ``M``.

    The public MCP surface stays at inspect/apply/render. These helpers only
    remove repeated generated boilerplate from apply batches.
    """

    def material(
        self,
        name: str,
        color: Sequence[float],
        roughness: float = 0.7,
        metallic: float = 0.0,
    ) -> bpy.types.Material:
        if not name or len(name) > 128:
            raise ValueError("helper_material_name")
        if len(color) not in {3, 4}:
            raise ValueError("helper_material_color")
        rgba = tuple(float(value) for value in color)
        if len(rgba) == 3:
            rgba = rgba + (1.0,)
        material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
        material.diffuse_color = rgba
        material.use_nodes = True
        node = material.node_tree.nodes.get("Principled BSDF") if material.node_tree else None
        if node is not None:
            base = node.inputs.get("Base Color")
            if base is not None:
                base.default_value = rgba
            rough = node.inputs.get("Roughness")
            if rough is not None:
                rough.default_value = max(0.0, min(1.0, float(roughness)))
            metal = node.inputs.get("Metallic")
            if metal is not None:
                metal.default_value = max(0.0, min(1.0, float(metallic)))
        return material

    def mesh(
        self,
        name: str,
        vertices: Sequence[Sequence[float]],
        faces: Sequence[Sequence[int]],
        collection: str | None = None,
        material: str | None = None,
        smooth: bool = True,
    ) -> bpy.types.Object:
        if not name or len(name) > 128:
            raise ValueError("helper_object_name")
        _validate_geometry(vertices, faces)
        verts = [tuple(float(value) for value in row[:3]) for row in vertices]
        face_rows = [tuple(int(value) for value in row) for row in faces]
        if any(len(row) < 3 for row in face_rows):
            raise ValueError("helper_face_invalid")

        obj = bpy.data.objects.get(name)
        if obj is not None and obj.type != "MESH":
            raise ValueError(f"helper_object_not_mesh:{name}")

        mesh = bpy.data.meshes.new(f"{name}_Mesh")
        mesh.from_pydata(verts, [], face_rows)
        mesh.update()

        if obj is None:
            obj = bpy.data.objects.new(name, mesh)
            target = _collection(collection)
            target.objects.link(obj)
        else:
            old = obj.data
            obj.data = mesh
            if collection:
                target = _collection(collection)
                if target not in obj.users_collection:
                    target.objects.link(obj)
            if old is not None and old.users == 0:
                bpy.data.meshes.remove(old)

        chosen = _material(material)
        if chosen is not None:
            mesh.materials.clear()
            mesh.materials.append(chosen)
        for polygon in mesh.polygons:
            polygon.use_smooth = bool(smooth)
        return obj

    def tube(
        self,
        name: str,
        points: Sequence[Sequence[float]],
        radius: float | Sequence[float],
        collection: str | None = None,
        material: str | None = None,
        sides: int = 8,
        smooth: bool = True,
    ) -> bpy.types.Object:
        if len(points) < 2 or len(points) > MAX_PATH_POINTS:
            raise ValueError("helper_path_points")
        if sides < 3 or sides > MAX_SIDES:
            raise ValueError("helper_sides")
        centers = [Vector(tuple(float(value) for value in row[:3])) for row in points]
        radii = [float(radius)] * len(centers) if isinstance(radius, (int, float)) else [float(value) for value in radius]
        if len(radii) != len(centers) or any(value <= 0 for value in radii):
            raise ValueError("helper_radius")

        vertices: list[tuple[float, float, float]] = []
        faces: list[tuple[int, ...]] = []
        for index, center in enumerate(centers):
            u, v = _frame(centers, index)
            for side in range(sides):
                angle = 2.0 * math.pi * side / sides
                point = center + radii[index] * (math.cos(angle) * u + math.sin(angle) * v)
                vertices.append(tuple(point))
        for index in range(len(centers) - 1):
            for side in range(sides):
                nxt = (side + 1) % sides
                a = index * sides + side
                b = index * sides + nxt
                faces.append((a, b, b + sides, a + sides))
        faces.append(tuple(reversed(range(sides))))
        faces.append(tuple((len(centers) - 1) * sides + side for side in range(sides)))
        return self.mesh(name, vertices, faces, collection, material, smooth)

    def clump(
        self,
        name: str,
        profiles: Sequence[Sequence[float]],
        collection: str | None = None,
        material: str | None = None,
        sides: int = 8,
        smooth: bool = True,
    ) -> bpy.types.Object:
        """Create a tapered elliptical clump from [x,y,z,width,depth] rows."""
        if len(profiles) < 2 or len(profiles) > MAX_PATH_POINTS:
            raise ValueError("helper_profiles")
        if sides < 3 or sides > MAX_SIDES:
            raise ValueError("helper_sides")
        centers = [Vector((float(row[0]), float(row[1]), float(row[2]))) for row in profiles]
        widths = [max(0.0002, float(row[3])) for row in profiles]
        depths = [max(0.0002, float(row[4])) for row in profiles]

        vertices: list[tuple[float, float, float]] = []
        faces: list[tuple[int, ...]] = []
        for index, center in enumerate(centers):
            u, v = _frame(centers, index)
            for side in range(sides):
                angle = 2.0 * math.pi * side / sides
                point = center + widths[index] * math.cos(angle) * u + depths[index] * math.sin(angle) * v
                vertices.append(tuple(point))
        for index in range(len(centers) - 1):
            for side in range(sides):
                nxt = (side + 1) % sides
                a = index * sides + side
                b = index * sides + nxt
                faces.append((a, b, b + sides, a + sides))
        faces.append(tuple(reversed(range(sides))))
        faces.append(tuple((len(centers) - 1) * sides + side for side in range(sides)))
        return self.mesh(name, vertices, faces, collection, material, smooth)

    def panel(
        self,
        name: str,
        points: Sequence[Sequence[float]],
        depth: float,
        collection: str | None = None,
        material: str | None = None,
        axis: str = "y",
        smooth: bool = False,
    ) -> bpy.types.Object:
        if len(points) < 3 or len(points) > MAX_PATH_POINTS:
            raise ValueError("helper_panel_points")
        axes = {"x": Vector((1.0, 0.0, 0.0)), "y": Vector((0.0, 1.0, 0.0)), "z": Vector((0.0, 0.0, 1.0))}
        if axis not in axes:
            raise ValueError("helper_panel_axis")
        offset = axes[axis] * float(depth)
        front = [Vector(tuple(float(value) for value in row[:3])) for row in points]
        vertices = [tuple(point) for point in front] + [tuple(point + offset) for point in front]
        count = len(front)
        faces: list[tuple[int, ...]] = [tuple(range(count)), tuple(reversed(range(count, count * 2)))]
        for index in range(count):
            nxt = (index + 1) % count
            faces.append((index, nxt, count + nxt, count + index))
        return self.mesh(name, vertices, faces, collection, material, smooth)

    def ellipsoid(
        self,
        name: str,
        center: Sequence[float],
        radii: Sequence[float],
        collection: str | None = None,
        material: str | None = None,
        segments: int = 16,
        rings: int = 8,
        smooth: bool = True,
    ) -> bpy.types.Object:
        if segments < 6 or segments > MAX_SIDES or rings < 3 or rings > 32:
            raise ValueError("helper_ellipsoid_resolution")
        if len(center) < 3 or len(radii) < 3:
            raise ValueError("helper_ellipsoid_shape")
        cx, cy, cz = (float(value) for value in center[:3])
        rx, ry, rz = (float(value) for value in radii[:3])
        if min(rx, ry, rz) <= 0:
            raise ValueError("helper_ellipsoid_radius")

        vertices: list[tuple[float, float, float]] = [(cx, cy, cz - rz)]
        faces: list[tuple[int, ...]] = []
        for ring in range(1, rings + 1):
            polar = math.pi * ring / (rings + 1)
            sp = math.sin(polar)
            cp = math.cos(polar)
            for segment in range(segments):
                angle = 2.0 * math.pi * segment / segments
                vertices.append((cx + rx * sp * math.cos(angle), cy + ry * sp * math.sin(angle), cz - rz * cp))
        top = len(vertices)
        vertices.append((cx, cy, cz + rz))

        for segment in range(segments):
            nxt = (segment + 1) % segments
            faces.append((0, 1 + nxt, 1 + segment))
        for ring in range(rings - 1):
            base = 1 + ring * segments
            next_base = base + segments
            for segment in range(segments):
                nxt = (segment + 1) % segments
                faces.append((base + segment, base + nxt, next_base + nxt, next_base + segment))
        last = 1 + (rings - 1) * segments
        for segment in range(segments):
            nxt = (segment + 1) % segments
            faces.append((last + segment, last + nxt, top))
        return self.mesh(name, vertices, faces, collection, material, smooth)


M = ModelingHelpers()
