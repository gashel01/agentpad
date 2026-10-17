"""agentpad keycap set: low profile with flat top, raised symbol, thicknesses designed for backlighting.
Reference: key base at z=0, center at (0,0), MX stem pointing down. Units = mm."""
import bpy, bmesh, math
from mathutils import Vector, Matrix

COLL = 'V2_Keycaps'
B, T, H = 18.0, 17.4, 8.0             # width at bottom, at top, height (flat top) -- 8 mm (Gael 2026-10-14), walls and top thickness unchanged
RB, RT, FIL = 2.6, 3.0, 1.0           # corner radii (bottom, top), top edge fillet
SIDE_BOT, SIDE_TOP = 1.25, 2.0        # thin wall at the bottom (15.5 opening: the switch housing passes through at the end of travel),
                                      # thick at the top (light exits through the top, not through the sides)
EMB = 0.5                             # relief height
STEM_D, CROSS_L, CROSS_W, CROSS_H = 6.0, 4.25, 1.45, 4.0  # cross + clearance (4.1 x 1.35 hard spec, Gael 2026-10-14); stem 6.0: 0.87 mm around
LEAD, LEAD_H = 0.4, 0.6              # flared entry: the 1st layer ("elephant's foot", stem resting on the bed) tightens the cross
ICONS = 'C:/Windows/Fonts/SegoeIcons.ttf'
DIGITS = 'C:/Windows/Fonts/seguisb.ttf'

if COLL not in bpy.data.collections:
    c = bpy.data.collections.new(COLL); bpy.context.scene.collection.children.link(c)
coll = bpy.data.collections[COLL]


def rrect(w, r, z, seg=12):
    h = w / 2 - r; pts = []
    for cx, cy, a0 in ((h, h, 0), (-h, h, 90), (-h, -h, 180), (h, -h, 270)):
        for k in range(seg + 1):
            a = math.radians(a0 + 90 * k / seg); pts.append(Vector((cx + r * math.cos(a), cy + r * math.sin(a), z)))
    return pts


def loft(rings, name='c'):
    b = bmesh.new(); vr = [[b.verts.new(p) for p in r] for r in rings]; n = len(rings[0])
    for a, c in zip(vr, vr[1:]):
        for i in range(n):
            j = (i + 1) % n; b.faces.new((a[i], a[j], c[j], c[i]))
    b.faces.new(vr[0][::-1]); b.faces.new(vr[-1])
    bmesh.ops.recalc_face_normals(b, faces=b.faces[:])
    me = bpy.data.meshes.new(name); b.to_mesh(me); b.free()
    o = bpy.data.objects.new(name, me); coll.objects.link(o); return o


def cyl(d, z0, z1, seg=48):
    b = bmesh.new(); bmesh.ops.create_cone(b, cap_ends=True, segments=seg, radius1=d / 2, radius2=d / 2, depth=z1 - z0)
    bmesh.ops.translate(b, verts=b.verts[:], vec=(0, 0, (z0 + z1) / 2))
    me = bpy.data.meshes.new('c'); b.to_mesh(me); b.free()
    o = bpy.data.objects.new('c', me); coll.objects.link(o); return o


def box(sx, sy, z0, z1):
    b = bmesh.new(); bmesh.ops.create_cube(b, size=1)
    bmesh.ops.scale(b, vec=(sx, sy, z1 - z0), verts=b.verts[:]); bmesh.ops.translate(b, vec=(0, 0, (z0 + z1) / 2), verts=b.verts[:])
    me = bpy.data.meshes.new('c'); b.to_mesh(me); b.free()
    o = bpy.data.objects.new('c', me); coll.objects.link(o); return o


def cross(z0, z1, grow=0.0):
    l, w = (CROSS_L + grow) / 2, (CROSS_W + grow) / 2
    pts = [(l, w), (w, w), (w, l), (-w, l), (-w, w), (-l, w), (-l, -w), (-w, -w), (-w, -l), (w, -l), (w, -w), (l, -w)]
    b = bmesh.new(); bot = [b.verts.new((x, y, z0)) for x, y in pts]; top = [b.verts.new((x, y, z1)) for x, y in pts]
    b.faces.new(bot[::-1]); b.faces.new(top)
    for i in range(12):
        j = (i + 1) % 12; b.faces.new((bot[i], bot[j], top[j], top[i]))
    bmesh.ops.recalc_face_normals(b, faces=b.faces[:])
    me = bpy.data.meshes.new('c'); b.to_mesh(me); b.free()
    o = bpy.data.objects.new('c', me); coll.objects.link(o); return o


def nm(o):
    b = bmesh.new(); b.from_mesh(o.data); k = sum(1 for e in b.edges if not e.is_manifold); b.free(); return k


def op(base, c, kind, keep=False):
    old = base.data
    for solver in ('EXACT', 'FAST'):
        md = base.modifiers.new('b', 'BOOLEAN'); md.operation = kind; md.solver = solver; md.object = c
        me = bpy.data.meshes.new_from_object(base.evaluated_get(bpy.context.evaluated_depsgraph_get()))
        base.modifiers.clear(); base.data = me
        if nm(base) == 0 and len(me.polygons): break
        base.data = old; bpy.data.meshes.remove(me)
    else:
        raise RuntimeError('boolean %s %s' % (base.name, kind))
    bpy.data.meshes.remove(old)
    if not keep: bpy.data.objects.remove(c, do_unlink=True)


def outer_rings(inset=0.0, z_top=H, z_bot=0.0):
    """Body rings: sloped side then quarter-circle fillet up to the top."""
    rings = []
    def w_at(z): return B + (T - B) * (z - z_bot) / (z_top - z_bot)
    def r_at(z): return RB + (RT - RB) * (z - z_bot) / (z_top - z_bot)
    zf = z_top - FIL
    for z in (z_bot, zf):
        rings.append(rrect(w_at(z) - 2 * inset, max(0.4, r_at(z) - inset), z))
    for k in range(1, 7):
        a = math.radians(90 * k / 6); z = zf + FIL * math.sin(a); d = FIL * (1 - math.cos(a))
        rings.append(rrect(w_at(z) - 2 * inset - 2 * d, max(0.4, r_at(z) - inset - d), z))
    return rings


def glyph(text, font, size):
    """Symbol prism: 2D font outline, cleaned up, placed on top."""
    f = bpy.data.fonts.load(font, check_existing=True)
    cu = bpy.data.curves.new('g', 'FONT'); cu.font = f; cu.body = text; cu.size = size
    cu.align_x = 'CENTER'; cu.align_y = 'CENTER'; cu.extrude = 0.0; cu.resolution_u = 8; cu.fill_mode = 'BOTH'
    o = bpy.data.objects.new('g', cu); coll.objects.link(o)
    me = bpy.data.meshes.new_from_object(o.evaluated_get(bpy.context.evaluated_depsgraph_get()))
    bpy.data.objects.remove(o, do_unlink=True)
    b = bmesh.new(); b.from_mesh(me); bpy.data.meshes.remove(me)
    bmesh.ops.remove_doubles(b, verts=b.verts[:], dist=1e-3)
    bmesh.ops.dissolve_degenerate(b, edges=b.edges[:], dist=1e-3)
    xs = [v.co.x for v in b.verts]; ys = [v.co.y for v in b.verts]
    ctr = Vector(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, 0))
    for v in b.verts: v.co = v.co - ctr
    for fc in b.faces:
        if fc.normal.z < 0: fc.normal_flip()
    bottom = list(b.verts)
    ext = bmesh.ops.extrude_face_region(b, geom=b.faces[:])
    top = [e for e in ext['geom'] if isinstance(e, bmesh.types.BMVert)]
    for fc in [e for e in ext['geom'] if isinstance(e, bmesh.types.BMFace)]: pass
    for v in bottom: v.co.z = H - 0.2                                    # anchored 0.2 mm into the material
    for v in top: v.co.z = H + EMB                                       # relief of EMB above the flat top
    bmesh.ops.recalc_face_normals(b, faces=b.faces[:])
    me = bpy.data.meshes.new('g'); b.to_mesh(me); b.free()
    g = bpy.data.objects.new('g', me); coll.objects.link(g); return g


def keycap(name, top, symbol=None, sides=(SIDE_BOT, SIDE_TOP)):
    """top = top thickness; symbol = (text, font, size) or None."""
    old = bpy.data.objects.get(name)
    if old: bpy.data.objects.remove(old, do_unlink=True)
    cap = loft(outer_rings(), name)
    zc = H - top                                                                 # flat ceiling, top of thickness `top`
    w0, w1 = B - 2 * sides[0], T - 2 * sides[1]
    wb = w0 + (w0 - w1) / zc                                                     # extended to z = -1 (opens below the skirt)
    cav = loft([rrect(wb, 1.6, -1.0), rrect(w1, 1.4, zc)], 'cav')
    op(cap, cav, 'DIFFERENCE')
    op(cap, cyl(STEM_D, 0.0, zc + top / 2), 'UNION')                             # solid stem, fused at mid-thickness of the top (never beyond)
    op(cap, cross(-0.5, CROSS_H), 'DIFFERENCE')                                  # MX cross, as a single prism
    op(cap, cross(-0.5, LEAD_H, LEAD), 'DIFFERENCE')                             # flared entry of the cross
    if symbol:
        op(cap, glyph(*symbol), 'UNION')
    cap.data.shade_smooth(); cap.data.set_sharp_from_angle(angle=math.radians(35))
    return cap


AGENT_TOP, FRONT_TOP = 0.6, 1.6   # agents: top 0.6 = bridge + 2 solid layers at 0.2 (0.2 alone doesn't print: 15 mm bridge)
AGENT_SIDES = (1.2, 1.2)          # agents: thin sides too, light passes through the sides
caps = []
caps.append(keycap('KC_agent_lisse', AGENT_TOP, None, AGENT_SIDES))
for n in range(1, 9):
    caps.append(keycap('KC_agent_%d' % n, AGENT_TOP, (str(n), DIGITS, 12.5), AGENT_SIDES))
for nm_, cp in (('KC_eclair', 0xE945), ('KC_check', 0xE930), ('KC_croix', 0xEA39), ('KC_micro', 0xE720)):
    caps.append(keycap(nm_, FRONT_TOP, (chr(cp), ICONS, 10.0)))
for i, o in enumerate(caps):
    o.location = (-260 + (i % 5) * 24, 260 - (i // 5) * 24, 0)
for o in [o for o in coll.objects if o.name in ('c', 'cav', 'g') or o.name.startswith(('c.', 'cav.', 'g.'))]:
    bpy.data.objects.remove(o, do_unlink=True)
print([(o.name, nm(o), len(o.data.polygons)) for o in caps])
