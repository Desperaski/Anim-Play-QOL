import bpy
from bpy.props import BoolProperty, PointerProperty, StringProperty, CollectionProperty, IntProperty
from bpy.types import Panel, PropertyGroup, UIList
from bpy.app.handlers import persistent

# ---------- state ----------
_msgbus_owner = object()
_subscribed = set()          # int-указатели объектных animation_data, на которые подписаны
_last_changed_obj = None     # последний объект, где msgbus заметил смену action
_in_apply = False            # защита от рекурсии

# --- dev flags ---
DEV_SHOW_DIAGNOSTICS = False

# ---------- helpers ----------
def get_action_range(action, use_manual=False):
    if use_manual and getattr(action, "use_frame_range", False):
        return int(action.frame_start), int(action.frame_end)
    try:
        start = int(action.curve_frame_range[0])
        end = int(action.curve_frame_range[1])
    except Exception:
        try:
            start = int(action.frame_range[0])
            end = int(action.frame_range[1])
        except Exception:
            return 1, 250
    return (start, end) if end > start else (1, 250)


def _get_current_action_info():
    """Каким action'ом сейчас должен рулиться таймлайн."""
    global _last_changed_obj

    # 1) Активный объект
    try:
        ao = bpy.context.active_object
        if ao is not None and ao.animation_data is not None and ao.animation_data.action is not None:
            return ao.animation_data.action, ao
    except Exception:
        pass

    # 2) Последний «переключённый» msgbus-ом
    if _last_changed_obj is not None:
        try:
            ad = _last_changed_obj.animation_data
            if ad is not None and ad.action is not None:
                return ad.action, _last_changed_obj
        except ReferenceError:
            _last_changed_obj = None
        except Exception:
            pass

    # 3) Любой объект с action
    try:
        for o in bpy.data.objects:
            ad = o.animation_data
            if ad is not None and ad.action is not None:
                return ad.action, o
    except Exception:
        pass

    return None, None


def _apply_action_range(scene):
    global _in_apply
    if _in_apply:
        return
    _in_apply = True
    try:
        try:
            props = scene.animqol_props
        except AttributeError:
            return
        if not props.auto_fit_range:
            return
        action, _ = _get_current_action_info()
        if action is None:
            return
        start, end = get_action_range(action, props.use_manual_range)
        if scene.frame_start != start or scene.frame_end != end:
            scene.frame_start = start
            scene.frame_end = end
    finally:
        _in_apply = False


def _apply_all_scenes():
    try:
        scenes = bpy.data.scenes
    except AttributeError:
        return
    for scene in scenes:
        try:
            _apply_action_range(scene)
        except Exception:
            pass


# ---------- msgbus ----------
def _on_action_changed_ptr(obj_ptr):
    global _last_changed_obj
    try:
        for o in bpy.data.objects:
            try:
                if o.as_pointer() != obj_ptr:
                    continue
            except Exception:
                continue
            if o.animation_data is not None and o.animation_data.action is not None:
                _last_changed_obj = o
            break
    except Exception:
        pass
    _apply_all_scenes()


def _subscribe_object(obj):
    if obj is None or obj.animation_data is None:
        return
    try:
        key = obj.as_pointer()
    except Exception:
        return
    if key in _subscribed:
        return
    try:
        bpy.msgbus.subscribe_rna(
            key=(obj.animation_data, "action"),
            owner=_msgbus_owner,
            args=(key,),
            notify=_on_action_changed_ptr,
        )
        _subscribed.add(key)
    except Exception:
        pass


def _ensure_subscriptions():
    try:
        objs = bpy.data.objects
    except AttributeError:
        return
    for obj in objs:
        try:
            _subscribe_object(obj)
        except Exception:
            pass


def _resubscribe_all():
    global _subscribed
    try:
        bpy.msgbus.clear_by_owner(_msgbus_owner)
    except Exception:
        pass
    _subscribed = set()
    _ensure_subscriptions()


# ---------- deferred init ----------
# bpy.data ещё ограничен в register()
def _deferred_init():
    _resubscribe_all()
    _apply_all_scenes()
    return None  # one-shot


# ---------- handlers ----------
@persistent
def _on_load_post(*args):
    _resubscribe_all()
    _apply_all_scenes()


@persistent
def _on_undo_redo_post(*args):
    _resubscribe_all()
    _apply_all_scenes()


def update_frame_range_from_action(scene, depsgraph):
    _ensure_subscriptions()   # подхватываем новые объекты
    _apply_action_range(scene)


def check_playback_loop(scene, depsgraph):
    _apply_action_range(scene)
    try:
        screen = bpy.context.screen
    except Exception:
        screen = None
    props = getattr(scene, "animqol_props", None)
    if props is None:
        return
    if screen and screen.is_animation_playing and not props.loop_enabled:
        if scene.frame_current >= scene.frame_end:
            bpy.ops.screen.animation_cancel()
            scene.frame_set(scene.frame_start)


# ---------- props ----------
class AnimPlayActionItem(PropertyGroup):
    name: StringProperty()


def on_target_object_changed(self, context):
    # target больше не двигает таймлайн — только гарантируем подписку
    _ensure_subscriptions()


def force_update_range(self, context):
    scene = context.scene
    props = scene.animqol_props
    if not props.auto_fit_range:
        scene.frame_start = 1
        scene.frame_end = 250
        return
    _apply_action_range(scene)


def update_selected_action(self, context):
    props = context.scene.animqol_props
    obj = props.target_object
    if obj and obj.animation_data and 0 <= props.action_list_index < len(props.action_list):
        action_name = props.action_list[props.action_list_index].name
        act = bpy.data.actions.get(action_name)
        if act:
            obj.animation_data.action = act
            for window in context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.tag_redraw()


class AnimPlayProps(PropertyGroup):
    target_object: PointerProperty(
        name="Target Object",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'ARMATURE' and obj.animation_data is not None,
        description="Select the armature to view animations",
        update=on_target_object_changed,
    )
    loop_enabled: BoolProperty(name="Loop Animation", default=True)
    auto_fit_range: BoolProperty(name="Auto-fit Range", default=True, update=force_update_range)
    use_manual_range: BoolProperty(name="Prefer Manual Range", default=True, update=force_update_range)
    search_text: StringProperty(name="Search", default="")
    action_list: CollectionProperty(type=AnimPlayActionItem)
    action_list_index: IntProperty(default=0, update=update_selected_action)


# ---------- UIList ----------
class ANIMQOL_UL_action_list(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.name, icon='ACTION')


# ---------- operators ----------
class ANIMQOL_OT_refresh_list(bpy.types.Operator):
    bl_idname = "animqol.refresh_list"
    bl_label = "Refresh List"

    def execute(self, context):
        props = context.scene.animqol_props
        props.action_list.clear()
        search_lower = props.search_text.lower()
        for action in bpy.data.actions:
            if search_lower in action.name.lower():
                item = props.action_list.add()
                item.name = action.name
        obj = props.target_object
        if obj and obj.animation_data and obj.animation_data.action:
            current_name = obj.animation_data.action.name
            for i, item in enumerate(props.action_list):
                if item.name == current_name:
                    props.action_list_index = i
                    break
        return {'FINISHED'}


class ANIMQOL_OT_unlink_action(bpy.types.Operator):
    bl_idname = "animqol.unlink_action"
    bl_label = "Unlink Action"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.animqol_props
        obj = props.target_object
        if obj and obj.animation_data:
            obj.animation_data.action = None
        return {'FINISHED'}


class ANIMQOL_OT_search_window(bpy.types.Operator):
    bl_idname = "animqol.search_window"
    bl_label = "Search Action"

    def invoke(self, context, event):
        props = context.scene.animqol_props
        props.action_list.clear()
        for action in bpy.data.actions:
            item = props.action_list.add()
            item.name = action.name
        obj = props.target_object
        if obj and obj.animation_data and obj.animation_data.action:
            current_name = obj.animation_data.action.name
            for i, item in enumerate(props.action_list):
                if item.name == current_name:
                    props.action_list_index = i
                    break
        return context.window_manager.invoke_props_dialog(self, width=400)

    def draw(self, context):
        layout = self.layout
        props = context.scene.animqol_props
        row = layout.row(align=True)
        row.prop(props, "search_text", text="")
        row.operator("animqol.refresh_list", text="", icon='FILE_REFRESH')
        layout.separator()
        layout.template_list("ANIMQOL_UL_action_list", "", props, "action_list",
                             props, "action_list_index", rows=10)

    def execute(self, context):
        return {'FINISHED'}


class ANIMQOL_OT_diagnose(bpy.types.Operator):
    bl_idname = "animqol.diagnose"
    bl_label = "Copy Diagnostics"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return DEV_SHOW_DIAGNOSTICS

    def execute(self, context):
        lines = []
        lines.append("=== Anim Play QOL Diagnostics ===")
        lines.append(f"Blender: {bpy.app.version_string}")
        lines.append(f"Objects total: {len(bpy.data.objects)}")
        lines.append(f"Scenes: {[s.name for s in bpy.data.scenes]}")

        try:
            active = context.active_object
            lines.append(f"active_object: {active.name if active else None}")
        except Exception as e:
            lines.append(f"active_object: ERR {e}")

        lines.append("")
        lines.append("--- Scene state ---")
        for scene in bpy.data.scenes:
            try:
                p = scene.animqol_props
                lines.append(f"[{scene.name}] range=({scene.frame_start},{scene.frame_end}) cur={scene.frame_current}")
                lines.append(f"  auto_fit={p.auto_fit_range} use_manual={p.use_manual_range}")
                lines.append(f"  target_object={p.target_object.name if p.target_object else None}")
            except Exception as e:
                lines.append(f"[{scene.name}] ERR {e}")

        lines.append("")
        lines.append(f"--- Msgbus subs: {len(_subscribed)} ---")
        lines.append(f"_last_changed_obj: {getattr(_last_changed_obj, 'name', None)}")

        lines.append("")
        lines.append("--- Objects with actions ---")
        for obj in bpy.data.objects:
            try:
                ad = obj.animation_data
                if ad is not None and ad.action is not None:
                    act = ad.action
                    try:
                        crv = tuple(act.curve_frame_range)
                    except Exception as e:
                        crv = f"ERR {e}"
                    lines.append(
                        f"  '{obj.name}' -> action='{act.name}' "
                        f"curve_range={crv} frame_range=({act.frame_start},{act.frame_end}) "
                        f"use_frame_range={getattr(act,'use_frame_range','?')}"
                    )
            except Exception as e:
                lines.append(f"  '{getattr(obj,'name','?')}' ERR {e}")

        lines.append("")
        lines.append("--- _get_current_action_info() ---")
        try:
            a, o = _get_current_action_info()
            lines.append(f"  action={a.name if a else None} obj={o.name if o else None}")
        except Exception as e:
            lines.append(f"  ERR {e}")

        text = "\n".join(lines)
        print(text)
        try:
            context.window_manager.clipboard = text
            self.report({'INFO'}, "Diagnostics copied to clipboard (also in console)")
        except Exception:
            self.report({'INFO'}, "Diagnostics printed to console")
        return {'FINISHED'}


# ---------- panel ----------
class ANIMQOL_PT_main_panel(Panel):
    bl_label = "Anim Play QOL"
    bl_idname = "ANIMQOL_PT_main_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Marionette"
    bl_order = 30

    def draw(self, context):
        layout = self.layout
        props = context.scene.animqol_props
        layout.prop(props, "target_object", text="")
        obj = props.target_object
        if obj and obj.animation_data:
            layout.separator()
            act = obj.animation_data.action
            row = layout.row(align=True)
            menu_text = act.name if act else "Search Action..."
            row.operator("animqol.search_window", text=menu_text, icon='VIEWZOOM')
            if act:
                row.operator("animqol.unlink_action", text="", icon='X')
            layout.separator()
            col = layout.column(align=True)
            row = col.row(align=True)
            row.scale_y = 1.5
            row.operator("screen.frame_jump", text="", icon='REW').end = False
            try:
                playing = bpy.context.screen.is_animation_playing
            except Exception:
                playing = False
            if playing:
                row.operator("screen.animation_play", text="Pause", icon='PAUSE')
            else:
                row.operator("screen.animation_play", text="Play", icon='PLAY')
            row.operator("screen.frame_jump", text="", icon='FF').end = True

            col.separator()
            col.prop(props, "loop_enabled", text="Loop")
            col.prop(props, "auto_fit_range", text="Auto-fit Range")
            subcol = col.column(align=True)
            subcol.enabled = props.auto_fit_range
            subcol.prop(props, "use_manual_range", text="Prefer Manual Range")

            if props.auto_fit_range:
                drv_action, drv_obj = _get_current_action_info()
                if drv_action is not None:
                    start, end = get_action_range(drv_action, props.use_manual_range)
                    manual = props.use_manual_range and getattr(drv_action, "use_frame_range", False)
                    src = f"  (from {drv_obj.name})" if drv_obj else ""
                    txt = f"Timeline: {start} — {end}{' [Manual]' if manual else ''}{src}"
                    col.label(text=txt, icon='PREVIEW_RANGE')

            col.separator()
            if DEV_SHOW_DIAGNOSTICS:
                col.operator("animqol.diagnose", text="Copy Diagnostics", icon='INFO')

classes = (
    AnimPlayActionItem,
    ANIMQOL_UL_action_list,
    AnimPlayProps,
    ANIMQOL_OT_refresh_list,
    ANIMQOL_OT_unlink_action,
    ANIMQOL_OT_search_window,
    ANIMQOL_OT_diagnose,
    ANIMQOL_PT_main_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.animqol_props = PointerProperty(type=AnimPlayProps)

    # register() получает _RestrictData — поэтому инициализация отложена таймером
    bpy.app.timers.register(_deferred_init, first_interval=0.1, persistent=True)

    bpy.app.handlers.depsgraph_update_post.append(update_frame_range_from_action)
    bpy.app.handlers.frame_change_post.append(check_playback_loop)
    bpy.app.handlers.load_post.append(_on_load_post)
    bpy.app.handlers.undo_post.append(_on_undo_redo_post)
    bpy.app.handlers.redo_post.append(_on_undo_redo_post)


def unregister():
    try:
        if bpy.app.timers.is_registered(_deferred_init):
            bpy.app.timers.unregister(_deferred_init)
    except Exception:
        pass
    try:
        bpy.msgbus.clear_by_owner(_msgbus_owner)
    except Exception:
        pass
    for handlers, fn in (
        (bpy.app.handlers.depsgraph_update_post, update_frame_range_from_action),
        (bpy.app.handlers.frame_change_post, check_playback_loop),
        (bpy.app.handlers.load_post, _on_load_post),
        (bpy.app.handlers.undo_post, _on_undo_redo_post),
        (bpy.app.handlers.redo_post, _on_undo_redo_post),
    ):
        if fn in handlers:
            handlers.remove(fn)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.animqol_props


if __name__ == "__main__":
    register()