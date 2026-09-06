# ============================================================================================================
# Basic Blender Bake
#
# Destructive texture-atlas baker for Blender. Always backup the file before applying a Bake.
#
# GitHub Repository: https://github.com/donitzo/basic-blender-bake
# Author: Donitz
# License: MIT
# ============================================================================================================

import bpy
import math

from array import array

bl_info = {
    'name': 'Basic Blender Bake',
    'author': 'Donitz',
    'version': (1, 0, 0),
    'blender': (5, 0, 0),
    'location': 'View3D > Sidebar > Bake',
    'description': 'Destructive texture-atlas baker',
    'category': 'Object',
}

def bake(
    objects,
    atlas_size=4096,
    bake_ao=True,
    bake_normal=True,
    bake_emission=True,
    bake_roughness=True,
    bake_lights=False,
    margin_pixels=4,
    unwrap_angle_limit_degrees=66,
    ao_distance=0.3,
    ao_strength=1.0,
    ao_samples=64,
    illumination_strength=1.0,
    lighting_samples=256,
):
    scene = bpy.context.scene

    objects_set = set(objects)
    external_objects = [obj for obj in scene.objects if obj not in objects_set]
    external_hide_render = { obj: obj.hide_render for obj in external_objects }

    for obj in external_objects:
        obj.hide_render = True

    # Find mesh objects

    mesh_objects = []

    for obj in objects:
        if not obj.hide_render and obj.type == 'MESH' and len(obj.material_slots) > 0 and any(
            slot.material is not None and slot.material.use_nodes
            for slot in obj.material_slots
        ):
            mesh_objects.append(obj)

    if not mesh_objects:
        raise ValueError('No mesh objects supplied')

    # Create the separate bake UV if it does not exist

    if bpy.context.object is not None:
        if bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

    for obj in mesh_objects:
        old_render_uv = None

        for uv_layer in obj.data.uv_layers:
            if uv_layer.active_render:
                old_render_uv = uv_layer
                break

        bake_uv = obj.data.uv_layers.get('BakeUV')
        if bake_uv is None:
            bake_uv = obj.data.uv_layers.new(name='BakeUV', do_init=False)
        bake_uv.active = True

        if old_render_uv is not None:
            old_render_uv.active_render = True

    # Select all meshes and all polygons

    bpy.ops.object.select_all(action='DESELECT')

    for bake_object in mesh_objects:
        bake_object.hide_set(False)
        bake_object.select_set(True)

    bpy.context.view_layer.objects.active = mesh_objects[0]

    bpy.context.scene.tool_settings.use_uv_select_sync = True

    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')

    # Smart UV unwrap all objects into separate islands split by steep angles

    bpy.ops.uv.smart_project(
        angle_limit=math.radians(unwrap_angle_limit_degrees),
        island_margin=0.0,
        area_weight=0.0,
        correct_aspect=True,
    )

    # Normalize island scale to roughly the same texel density

    bpy.ops.uv.average_islands_scale()

    # Re-pack the islands with a margin and fit it to the atlas

    bpy.ops.uv.pack_islands(
        rotate=True,
        scale=True,
        merge_overlap=False,
        margin_method='ADD',
        margin=float(margin_pixels) / float(atlas_size),
    )

    bpy.ops.object.mode_set(mode='OBJECT')

    # Get all the unique materials used by the objects

    source_materials = set()

    for obj in mesh_objects:
        for slot in obj.material_slots:
            if slot.material is not None and slot.material.use_nodes:
                source_materials.add(slot.material)
            else:
                print(f'Object "{obj.name}" has non-node material')

    # Create images

    color_image = bpy.data.images.new(
        name='DiffuseAtlas',
        width=atlas_size,
        height=atlas_size,
    )
    color_image.colorspace_settings.name = 'sRGB'

    if bake_ao:
        ao_image = bpy.data.images.new(
            name='__BakeAO__',
            width=atlas_size,
            height=atlas_size,
        )
        ao_image.colorspace_settings.name = 'Non-Color'

    if bake_normal:
        normal_image = bpy.data.images.new(
            name='NormalAtlas',
            width=atlas_size,
            height=atlas_size,
        )
        normal_image.colorspace_settings.name = 'Non-Color'

    bake_emission_illumination = bake_emission and illumination_strength > 0.0 and not bake_lights

    if bake_emission:
        if bake_emission_illumination:
            illumination_image = bpy.data.images.new(
                name='__BakeIllumination__',
                width=atlas_size,
                height=atlas_size,
            )
            illumination_image.colorspace_settings.name = 'sRGB'

        emission_image = bpy.data.images.new(
            name='EmissionAtlas',
            width=atlas_size,
            height=atlas_size,
        )
        emission_image.colorspace_settings.name = 'sRGB'

    if bake_roughness:
        roughness_image = bpy.data.images.new(
            name='RoughnessAtlas',
            width=atlas_size,
            height=atlas_size,
        )
        roughness_image.colorspace_settings.name = 'Non-Color'

    # Get the image target nodes (each material's bake target image in cycles)

    target_nodes = {}

    for material in source_materials:
        nodes = material.node_tree.nodes

        target = nodes.new('ShaderNodeTexImage')
        target_nodes[material] = target

    def set_bake_target(image):
        for material, target in target_nodes.items():
            nodes = material.node_tree.nodes

            for node in nodes:
                node.select = False

            target.image = image
            target.select = True
            nodes.active = target

    # Configure renderer

    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'GPU'

    if bake_ao:
        scene.world.light_settings.distance = ao_distance

    # Get objects marked as dynamic vs static

    local_objects = [obj for obj in mesh_objects if obj.get('dynamic', False)]
    global_objects = [obj for obj in mesh_objects if not obj.get('dynamic', False)]

    # Bake diffuse color

    set_bake_target(color_image)

    scene.cycles.samples = lighting_samples if bake_lights else 1

    bpy.ops.object.select_all(action='DESELECT')

    for obj in mesh_objects:
        obj.hide_render = obj in local_objects

    for obj in global_objects:
        obj.select_set(True)

    clear_color = True

    # Bake all static objects together

    if global_objects:
        bpy.context.view_layer.objects.active = global_objects[0]

        bpy.ops.object.bake(
            type='DIFFUSE',
            pass_filter={ 'COLOR', 'INDIRECT', 'DIRECT' } if bake_lights else { 'COLOR' },
            margin=margin_pixels,
            margin_type='ADJACENT_FACES',
            use_clear=clear_color,
            uv_layer='BakeUV',
        )
        clear_color = False

    # Bake each dynamic object separately without lighting

    for local_object in local_objects:
        for obj in mesh_objects:
            obj.hide_render = obj != local_object

        bpy.ops.object.select_all(action='DESELECT')

        local_object.hide_set(False)
        local_object.select_set(True)
        bpy.context.view_layer.objects.active = local_object

        scene.cycles.samples = 1

        bpy.ops.object.bake(
            type='DIFFUSE',
            pass_filter={ 'COLOR' },
            margin=margin_pixels,
            margin_type='ADJACENT_FACES',
            use_clear=clear_color,
            uv_layer='BakeUV',
        )

        clear_color = False

    for obj in mesh_objects:
        obj.hide_render = False

    bpy.ops.object.select_all(action='DESELECT')

    for bake_object in mesh_objects:
        bake_object.hide_set(False)
        bake_object.select_set(True)

    bpy.context.view_layer.objects.active = mesh_objects[0]

    # Bake normals

    if bake_normal:
        set_bake_target(normal_image)

        scene.cycles.samples = 1

        bpy.ops.object.bake(
            type='NORMAL',
            normal_space='TANGENT',
            margin=margin_pixels,
            margin_type='ADJACENT_FACES',
            use_clear=True,
            uv_layer='BakeUV',
        )

    if bake_emission:
        if bake_emission_illumination:
            # Remove lighting

            background = None
            world = scene.world
            if world is not None and world.use_nodes and world.node_tree is not None:
                background = world.node_tree.nodes.get('Background')
                if background is not None:
                    world_strength = background.inputs['Strength'].default_value
                    background.inputs['Strength'].default_value = 0.0

            light_hide_render = {}

            for obj in scene.objects:
                if obj.type == 'LIGHT':
                    light_hide_render[obj] = obj.hide_render
                    obj.hide_render = True

            # Bake emission only illumination

            set_bake_target(illumination_image)

            scene.cycles.samples = lighting_samples

            bpy.ops.object.select_all(action='DESELECT')

            for obj in mesh_objects:
                obj.hide_render = obj in local_objects

            for obj in global_objects:
                obj.select_set(True)

            clear_illumination = True

            if global_objects:
                bpy.context.view_layer.objects.active = global_objects[0]

                bpy.ops.object.bake(
                    type='DIFFUSE',
                    pass_filter={ 'INDIRECT', 'DIRECT' },
                    margin=margin_pixels,
                    margin_type='ADJACENT_FACES',
                    use_clear=clear_illumination,
                    uv_layer='BakeUV',
                )

                clear_illumination = False

            for local_object in local_objects:
                for obj in mesh_objects:
                    obj.hide_render = obj != local_object

                bpy.ops.object.select_all(action='DESELECT')

                local_object.hide_set(False)
                local_object.select_set(True)
                bpy.context.view_layer.objects.active = local_object

                bpy.ops.object.bake(
                    type='DIFFUSE',
                    pass_filter={ 'INDIRECT', 'DIRECT' },
                    margin=margin_pixels,
                    margin_type='ADJACENT_FACES',
                    use_clear=clear_illumination,
                    uv_layer='BakeUV',
                )

                clear_illumination = False

            for obj in mesh_objects:
                obj.hide_render = False

            bpy.ops.object.select_all(action='DESELECT')

            for bake_object in mesh_objects:
                bake_object.hide_set(False)
                bake_object.select_set(True)

            bpy.context.view_layer.objects.active = mesh_objects[0]

            # Reset lighting

            if background is not None:
                background.inputs['Strength'].default_value = world_strength

            for obj, hidden in light_hide_render.items():
                obj.hide_render = hidden

        # Bake emission

        set_bake_target(emission_image)

        scene.cycles.samples = 1

        bpy.ops.object.bake(
            type='EMIT',
            margin=margin_pixels,
            margin_type='ADJACENT_FACES',
            use_clear=True,
            uv_layer='BakeUV',
        )

    # Bake roughness

    if bake_roughness:
        set_bake_target(roughness_image)

        scene.cycles.samples = 1

        bpy.ops.object.bake(
            type='ROUGHNESS',
            margin=margin_pixels,
            margin_type='ADJACENT_FACES',
            use_clear=True,
            uv_layer='BakeUV',
        )

    if bake_ao:
        # Bake global ambient occlusion

        set_bake_target(ao_image)

        scene.cycles.samples = ao_samples

        bpy.ops.object.select_all(action='DESELECT')

        for obj in mesh_objects:
            obj.hide_render = obj in local_objects

        for obj in global_objects:
            obj.select_set(True)

        clear_ao = True

        if global_objects:
            bpy.context.view_layer.objects.active = global_objects[0]

            bpy.ops.object.bake(
                type='AO',
                margin=margin_pixels,
                margin_type='ADJACENT_FACES',
                use_clear=clear_ao,
                uv_layer='BakeUV',
            )
            clear_ao = False

        # Bake local ambient occlusion

        for local_object in local_objects:
            for obj in mesh_objects:
                obj.hide_render = obj != local_object

            bpy.ops.object.select_all(action='DESELECT')

            local_object.hide_set(False)
            local_object.select_set(True)
            bpy.context.view_layer.objects.active = local_object

            bpy.ops.object.bake(
                type='AO',
                margin=margin_pixels,
                margin_type='ADJACENT_FACES',
                use_clear=clear_ao,
                uv_layer='BakeUV',
            )
            clear_ao = False

        for obj in mesh_objects:
            obj.hide_render = False

    # Remove temporary image target nodes

    for material, target in target_nodes.items():
        material.node_tree.nodes.remove(target)

    # Make the BakeUV the real UV

    for obj in mesh_objects:
        bake_uv = obj.data.uv_layers.get('BakeUV')

        for uv_layer in list(obj.data.uv_layers):
            if uv_layer != bake_uv:
                obj.data.uv_layers.remove(uv_layer)

        bake_uv.active = True
        bake_uv.active_render = True

    # Combine color and ambient occlusion

    pixel_count = len(color_image.pixels)

    if bake_ao:
        color_pixels = array('f', [0.0]) * pixel_count
        ao_pixels = array('f', [0.0]) * pixel_count

        color_image.pixels.foreach_get(color_pixels)
        ao_image.pixels.foreach_get(ao_pixels)

        for i in range(0, pixel_count, 4):
            ao = ao_pixels[i]
            ao_factor = max(0.0, 1.0 + (ao - 1.0) * ao_strength)

            color_pixels[i] *= ao_factor
            color_pixels[i + 1] *= ao_factor
            color_pixels[i + 2] *= ao_factor

        color_image.pixels.foreach_set(color_pixels)
        color_image.update()

        del color_pixels
        del ao_pixels

        bpy.data.images.remove(ao_image)

    if bake_emission:
        emission_pixels = array('f', [0.0]) * pixel_count
        emission_image.pixels.foreach_get(emission_pixels)

        if bake_emission_illumination:
            illumination_pixels = array('f', [0.0]) * pixel_count
            illumination_image.pixels.foreach_get(illumination_pixels)

            for i in range(0, pixel_count, 4):
                emission_pixels[i] += illumination_pixels[i] * illumination_strength
                emission_pixels[i + 1] += illumination_pixels[i + 1] * illumination_strength
                emission_pixels[i + 2] += illumination_pixels[i + 2] * illumination_strength

            bpy.data.images.remove(illumination_image)

            del illumination_pixels

        emission_image.pixels.foreach_set(emission_pixels)
        emission_image.update()

        del emission_pixels

    # Create the baked material

    baked_material = bpy.data.materials.new(name='BakedDiffuse')
    baked_material.use_nodes = True

    nodes = baked_material.node_tree.nodes
    links = baked_material.node_tree.links

    nodes.clear()

    output = nodes.new('ShaderNodeOutputMaterial')
    principled = nodes.new('ShaderNodeBsdfPrincipled')
    texture = nodes.new('ShaderNodeTexImage')
    uv_map = nodes.new('ShaderNodeUVMap')

    texture.image = color_image
    uv_map.uv_map = 'BakeUV'

    principled.inputs['Metallic'].default_value = 0.0
    principled.inputs['Roughness'].default_value = 1.0

    links.new(uv_map.outputs['UV'], texture.inputs['Vector'])
    links.new(texture.outputs['Color'], principled.inputs['Base Color'])
    links.new(principled.outputs['BSDF'], output.inputs['Surface'])

    if bake_normal:
        normal_texture = nodes.new('ShaderNodeTexImage')
        normal_texture.image = normal_image

        normal_map = nodes.new('ShaderNodeNormalMap')
        normal_map.space = 'TANGENT'
        normal_map.uv_map = 'BakeUV'

        links.new(uv_map.outputs['UV'], normal_texture.inputs['Vector'])
        links.new(normal_texture.outputs['Color'], normal_map.inputs['Color'])
        links.new(normal_map.outputs['Normal'], principled.inputs['Normal'])

    if bake_emission:
        emission_texture = nodes.new('ShaderNodeTexImage')
        emission_texture.image = emission_image

        links.new(uv_map.outputs['UV'], emission_texture.inputs['Vector'])
        links.new(emission_texture.outputs['Color'], principled.inputs['Emission Color'])
        principled.inputs['Emission Strength'].default_value = 1.0

    if bake_roughness:
        roughness_texture = nodes.new('ShaderNodeTexImage')
        roughness_texture.image = roughness_image

        links.new(uv_map.outputs['UV'], roughness_texture.inputs['Vector'])
        links.new(roughness_texture.outputs['Color'], principled.inputs['Roughness'])

    # Clear old materials and append the new material

    for obj in mesh_objects:
        obj.data.materials.clear()
        obj.data.materials.append(baked_material)

        for polygon in obj.data.polygons:
            polygon.material_index = 0

    for obj, hidden in external_hide_render.items():
        obj.hide_render = hidden

    print(f'Baked {len(mesh_objects)} objects to {atlas_size}x{atlas_size} atlas')

class OBJECT_OT_bake_atlas(bpy.types.Operator):
    bl_idname = 'object.bake_atlas'
    bl_label = 'Bake Atlas'
    bl_options = { 'REGISTER' }

    collection: bpy.props.StringProperty(
        name='Limit to Collection',
        default='',
    )

    atlas_size: bpy.props.IntProperty(
        name='Atlas Size',
        default=4096,
        min=128,
    )

    bake_ao: bpy.props.BoolProperty(
        name='Bake Ambient Occlusion',
        default=True,
    )

    bake_normal: bpy.props.BoolProperty(
        name='Bake Normal',
        default=True,
    )

    bake_emission: bpy.props.BoolProperty(
        name='Bake Emission (and illumination)',
        default=True,
    )

    bake_roughness: bpy.props.BoolProperty(
        name='Bake Roughness',
        default=True,
    )

    bake_lights: bpy.props.BoolProperty(
        name='Bake Lights',
        default=False,
    )

    margin_pixels: bpy.props.IntProperty(
        name='Margin Pixels',
        default=4,
        min=0,
    )

    unwrap_angle_limit_degrees: bpy.props.FloatProperty(
        name='Unwrap Angle Limit',
        default=66.0,
        min=0.0,
        max=180.0,
    )

    ao_distance: bpy.props.FloatProperty(
        name='AO Distance',
        default=0.3,
        min=0.0,
    )

    ao_strength: bpy.props.FloatProperty(
        name='AO Strength',
        default=1.0,
        min=0.0,
    )

    ao_samples: bpy.props.IntProperty(
        name='AO Samples',
        default=64,
        min=1,
    )

    illumination_strength: bpy.props.FloatProperty(
        name='Emission Illumination Strength',
        default=1.0,
        min=0.0,
    )

    lighting_samples: bpy.props.IntProperty(
        name='Bake Lighting Samples',
        default=256,
        min=1,
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=400)

    def draw(self, context):
        layout = self.layout

        warning_box = layout.box()
        warning_box.alert = True

        row = warning_box.row()

        row.label(text='Warning: This bake is destructive for both materials and UVs.', icon='ERROR')
        warning_box.label(text='Backup your .blend file before continuing, and bake as the final step.')

        info_box = layout.box()
        row = info_box.row()

        info_box.label(text='Add a Boolean property "dynamic" as true to bake objects separately.')

        layout.separator()

        layout.prop(self, 'collection')

        layout.separator()

        layout.prop(self, 'atlas_size')

        layout.separator()

        layout.prop(self, 'bake_ao')
        layout.prop(self, 'bake_normal')
        layout.prop(self, 'bake_emission')
        layout.prop(self, 'bake_roughness')
        layout.prop(self, 'bake_lights')

        layout.separator()

        layout.prop(self, 'margin_pixels')
        layout.prop(self, 'unwrap_angle_limit_degrees')

        layout.separator()

        column = layout.column()
        column.enabled = self.bake_ao
        column.prop(self, 'ao_distance')
        column.prop(self, 'ao_strength')
        column.prop(self, 'ao_samples')

        layout.separator()

        row = layout.row()
        row.enabled = self.bake_emission and not self.bake_lights
        row.prop(self, 'illumination_strength')
        layout.prop(self, 'lighting_samples')

    def execute(self, context):
        if self.collection != '':
            collection = bpy.data.collections.get(self.collection)

            if collection is None:
                self.report({ 'ERROR' }, f'Collection "{self.collection}" not found')
                return { 'CANCELLED' }

            objects = collection.all_objects
        else:
            objects = context.scene.objects

        bake(
            objects,
            atlas_size=self.atlas_size,
            bake_ao=self.bake_ao,
            bake_normal=self.bake_normal,
            bake_emission=self.bake_emission,
            bake_roughness=self.bake_roughness,
            bake_lights=self.bake_lights,
            margin_pixels=self.margin_pixels,
            unwrap_angle_limit_degrees=self.unwrap_angle_limit_degrees,
            ao_distance=self.ao_distance,
            ao_strength=self.ao_strength,
            ao_samples=self.ao_samples,
            illumination_strength=self.illumination_strength,
            lighting_samples=self.lighting_samples,
        )

        return { 'FINISHED' }

class VIEW3D_PT_bake_atlas(bpy.types.Panel):
    bl_label = 'Bake Texture Atlas'
    bl_idname = 'VIEW3D_PT_bake_atlas'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Bake'

    def draw(self, context):
        layout = self.layout
        layout.operator('object.bake_atlas')

classes = (
    OBJECT_OT_bake_atlas,
    VIEW3D_PT_bake_atlas,
)

def register():
    for c in classes:
        bpy.utils.register_class(c)

def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)

if __name__ == '__main__':
    register()
