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
import numpy as np

from array import array

bl_info = {
    'name': 'Basic Blender Bake',
    'author': 'Donitz',
    'version': (1, 0, 1),
    'blender': (5, 0, 0),
    'location': 'View3D > Sidebar > Bake',
    'description': 'Destructive texture-atlas baker',
    'category': 'Object',
}

def ensure_packages():
    import importlib.util
    import os
    import subprocess
    import sys

    packages_dir = os.path.join(
        bpy.utils.user_resource('SCRIPTS'),
        'basic_blender_bake_packages',
    )

    if packages_dir not in sys.path:
        sys.path.insert(0, packages_dir)

    if importlib.util.find_spec('rectpack') is None:
        print('Installing missing "rectpack" package...')

        os.makedirs(packages_dir, exist_ok=True)

        subprocess.check_call([
            sys.executable,
            '-m',
            'pip',
            'install',
            '--target',
            packages_dir,
            'rectpack',
        ])

        importlib.invalidate_caches()

def pack_atlases(
    objects,
    atlas_size=4096,
    desired_texel_world_size=0.005,
    margin_pixels=4,
    unwrap_angle_limit_degrees=66,
):
    import rectpack

    rects = []

    bpy.context.scene.tool_settings.use_uv_select_sync = True

    for obj in objects:
        obj['atlas_index'] = -1

        mesh = obj.data

        # UV unwrap the object

        if bpy.context.object is not None:
            if bpy.context.object.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.select_all(action='DESELECT')

        obj.hide_set(False)
        obj.select_set(True)

        bpy.context.view_layer.objects.active = obj

        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.reveal()
        bpy.ops.mesh.select_all(action='SELECT')

        bpy.ops.uv.smart_project(
            angle_limit=math.radians(unwrap_angle_limit_degrees),
            island_margin=0.0,
            area_weight=0.0,
            correct_aspect=True,
        )

        bpy.ops.uv.average_islands_scale()

        effective_pixels_per_uv_unit = None

        invalid_geometry = False

        for real_margin in [False, True]:
            bpy.ops.object.mode_set(mode='EDIT')

            bpy.ops.uv.pack_islands(
                rotate=True,
                scale=not real_margin,
                merge_overlap=False,
                margin_method='ADD',
                margin=float(margin_pixels) / effective_pixels_per_uv_unit if real_margin else 0.0,
            )

            bpy.ops.object.mode_set(mode='OBJECT')
            mesh.calc_loop_triangles()

            # Measure the UV size

            min_u = float('inf')
            min_v = float('inf')
            max_u = float('-inf')
            max_v = float('-inf')

            uv_layer = mesh.uv_layers['BakeUV']

            for loop in mesh.loops:
                uv = uv_layer.data[loop.index].uv

                min_u = min(min_u, uv.x)
                min_v = min(min_v, uv.y)
                max_u = max(max_u, uv.x)
                max_v = max(max_v, uv.y)

            uv_width = max_u - min_u
            uv_height = max_v - min_v

            # Measure world surface area represented by the UVs

            total_world_area = 0.0
            total_uv_area = 0.0

            world_matrix = obj.matrix_world

            for triangle in obj.data.loop_triangles:
                v0 = world_matrix @ mesh.vertices[triangle.vertices[0]].co
                v1 = world_matrix @ mesh.vertices[triangle.vertices[1]].co
                v2 = world_matrix @ mesh.vertices[triangle.vertices[2]].co

                world_area = (v1 - v0).cross(v2 - v0).length * 0.5

                uv0 = uv_layer.data[triangle.loops[0]].uv
                uv1 = uv_layer.data[triangle.loops[1]].uv
                uv2 = uv_layer.data[triangle.loops[2]].uv

                uv_area = abs(
                    (uv1.x - uv0.x) * (uv2.y - uv0.y)
                    - (uv1.y - uv0.y) * (uv2.x - uv0.x)
                ) * 0.5

                total_world_area += world_area
                total_uv_area += uv_area

            if total_world_area <= 0.0 or total_uv_area <= 0.0:
                print(f'Object "{obj.name}" has invalid geometry')
                invalid_geometry = True
                break

            desired_pixel_area = total_world_area / (desired_texel_world_size * desired_texel_world_size)
            pixels_per_uv_unit = math.sqrt(desired_pixel_area / total_uv_area)
            effective_pixels_per_uv_unit = pixels_per_uv_unit

            # Calculate the desired pixel size for the UV rectangle

            max_content_size = atlas_size - margin_pixels * 2

            pixel_width = math.ceil(uv_width * pixels_per_uv_unit)
            pixel_height = math.ceil(uv_height * pixels_per_uv_unit)

            if pixel_width > max_content_size or pixel_height > max_content_size:
                scale = min(max_content_size / pixel_width, max_content_size / pixel_height)

                pixel_width = max(1, min(max_content_size, math.floor(pixel_width * scale)))
                pixel_height = max(1, min(max_content_size, math.floor(pixel_height * scale)))

                effective_pixels_per_uv_unit *= scale

                if real_margin:
                    print(f'Object "{obj.name}" is too large to fit into one texture atlas and will have a lower resolution')

        if invalid_geometry:
            continue

        packed_width = pixel_width + margin_pixels * 2
        packed_height = pixel_height + margin_pixels * 2

        rects.append({
            'object': obj,
            'uv_min': (min_u, min_v),
            'uv_size': (uv_width, uv_height),
            'pixel_width': pixel_width,
            'pixel_height': pixel_height,
            'packed_width': packed_width,
            'packed_height': packed_height,
        })

    # Pack the UV rectangles into multiple atlases

    packer = rectpack.newPacker(rotation=False)

    for rect_index, rect in enumerate(rects):
        packer.add_rect(
            rect['packed_width'],
            rect['packed_height'],
            rid=rect_index,
        )

    packer.add_bin(
        atlas_size,
        atlas_size,
        count=len(rects),
    )

    packer.pack()

    # Modify the UV coordinates of each object to map the local atlas coordinates

    for bin_index, x, y, width, height, rect_index in packer.rect_list():
        rect = rects[rect_index]
        obj = rect['object']

        uv_layer = obj.data.uv_layers['BakeUV']

        min_u, min_v = rect['uv_min']
        uv_width, uv_height = rect['uv_size']

        pixel_width = rect['pixel_width']
        pixel_height = rect['pixel_height']

        content_x = x + margin_pixels
        content_y = y + margin_pixels

        for loop in obj.data.loops:
            uv = uv_layer.data[loop.index].uv

            normalized_u = (uv.x - min_u) / uv_width
            normalized_v = (uv.y - min_v) / uv_height

            uv.x = (content_x + normalized_u * pixel_width) / atlas_size
            uv.y = (content_y + normalized_v * pixel_height) / atlas_size

        # Store the atlas index for this object

        obj['atlas_index'] = bin_index

    return len(packer)

def bake(
    objects,
    atlas_size=4096,
    desired_texel_world_size=0.005,
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
    lighting_samples=1024,
    use_nearest_filtering=False,
    progress=None,
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
        if (not obj.hide_render
            and obj.type == 'MESH'
            and len(obj.material_slots) > 0
            and not obj.get('no_bake', False)
            and any(slot.material is not None and slot.material.use_nodes for slot in obj.material_slots)):
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

    # Pack as many texture atlases as required

    atlas_count = pack_atlases(
        mesh_objects,
        atlas_size,
        desired_texel_world_size,
        margin_pixels,
        unwrap_angle_limit_degrees
    )

    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='DESELECT')

    # Configure renderer

    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'GPU'

    if bake_ao:
        scene.world.light_settings.distance = ao_distance

    # Create atlases

    bake_emission_illumination = bake_emission and illumination_strength > 0.0 and not bake_lights

    global_dynamic_objects = [obj for obj in mesh_objects if obj.get('dynamic', False)]
    global_static_objects = [obj for obj in mesh_objects if not obj.get('dynamic', False)]

    materials = []

    for atlas_index in range(atlas_count):
        atlas_objects = [obj for obj in mesh_objects if obj.get('atlas_index', -1) == atlas_index]

        dynamic_objects = [obj for obj in atlas_objects if obj.get('dynamic', False)]
        static_objects = [obj for obj in atlas_objects if not obj.get('dynamic', False)]

        # Create images

        color_image = bpy.data.images.new(
            name=f'DiffuseAtlas{atlas_index}',
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
                name=f'NormalAtlas{atlas_index}',
                width=atlas_size,
                height=atlas_size,
            )
            normal_image.colorspace_settings.name = 'Non-Color'

        if bake_emission:
            if bake_emission_illumination:
                illumination_image = bpy.data.images.new(
                    name='__BakeIllumination__',
                    width=atlas_size,
                    height=atlas_size,
                )
                illumination_image.colorspace_settings.name = 'sRGB'

            emission_image = bpy.data.images.new(
                name=f'EmissionAtlas{atlas_index}',
                width=atlas_size,
                height=atlas_size,
            )
            emission_image.colorspace_settings.name = 'sRGB'

        if bake_roughness:
            roughness_image = bpy.data.images.new(
                name=f'RoughnessAtlas{atlas_index}',
                width=atlas_size,
                height=atlas_size,
            )
            roughness_image.colorspace_settings.name = 'Non-Color'

        # Get all the unique materials used by the objects

        source_materials = set()

        for obj in atlas_objects:
            for slot in obj.material_slots:
                if slot.material is not None and slot.material.use_nodes:
                    source_materials.add(slot.material)
                else:
                    print(f'Object "{obj.name}" has non-node material')

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

        if progress is not None:
            progress(float(atlas_index) / atlas_count)

        # Bake diffuse color

        set_bake_target(color_image)

        scene.cycles.samples = lighting_samples if bake_lights else 1

        clear_color = True

        # Bake all static objects together

        if static_objects:
            for obj in global_static_objects:
                obj.hide_render = False
            for obj in global_dynamic_objects:
                obj.hide_render = True
            bpy.ops.object.select_all(action='DESELECT')
            for obj in static_objects:
                obj.select_set(True)
            bpy.context.view_layer.objects.active = static_objects[0]

            bpy.ops.object.bake(
                type='DIFFUSE',
                pass_filter={ 'COLOR', 'INDIRECT', 'DIRECT' } if bake_lights else { 'COLOR' },
                margin=margin_pixels,
                margin_type='ADJACENT_FACES',
                use_clear=clear_color,
                uv_layer='BakeUV',
                use_selected_to_active=False,
            )
            clear_color = False

        if progress is not None:
            progress(float(atlas_index + 1.0 / 6.0) / atlas_count)

        # Bake each dynamic object separately without lighting

        for dynamic_object in dynamic_objects:
            for obj in mesh_objects:
                obj.hide_render = obj != dynamic_object
            bpy.ops.object.select_all(action='DESELECT')
            dynamic_object.select_set(True)
            bpy.context.view_layer.objects.active = dynamic_object

            scene.cycles.samples = 1

            bpy.ops.object.bake(
                type='DIFFUSE',
                pass_filter={ 'COLOR' },
                margin=margin_pixels,
                margin_type='ADJACENT_FACES',
                use_clear=clear_color,
                uv_layer='BakeUV',
                use_selected_to_active=False,
            )

            clear_color = False

        if progress is not None:
            progress(float(atlas_index + 2.0 / 6.0) / atlas_count)

        # Bake normals

        for obj in atlas_objects:
            obj.hide_render = False
        bpy.ops.object.select_all(action='DESELECT')
        for obj in atlas_objects:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = atlas_objects[0]

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
                use_selected_to_active=False,
            )

        if progress is not None:
            progress(float(atlas_index + 3.0 / 6.0) / atlas_count)

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
                use_selected_to_active=False,
            )

        if progress is not None:
            progress(float(atlas_index + 4.0 / 6.0) / atlas_count)

        if bake_emission:
            if bake_emission_illumination:
                # Remove lighting

                background = None
                world = scene.world
                if world is not None and world.node_tree is not None:
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

                clear_illumination = True

                if static_objects:
                    for obj in mesh_objects:
                        obj.hide_render = obj in global_dynamic_objects
                    bpy.ops.object.select_all(action='DESELECT')
                    for obj in static_objects:
                        obj.select_set(True)
                    bpy.context.view_layer.objects.active = static_objects[0]

                    bpy.ops.object.bake(
                        type='DIFFUSE',
                        pass_filter={ 'INDIRECT', 'DIRECT' },
                        margin=margin_pixels,
                        margin_type='ADJACENT_FACES',
                        use_clear=clear_illumination,
                        uv_layer='BakeUV',
                        use_selected_to_active=False,
                    )

                    clear_illumination = False

                for dynamic_object in dynamic_objects:
                    for obj in mesh_objects:
                        obj.hide_render = obj != dynamic_object
                    bpy.ops.object.select_all(action='DESELECT')
                    dynamic_object.select_set(True)
                    bpy.context.view_layer.objects.active = dynamic_object

                    bpy.ops.object.bake(
                        type='DIFFUSE',
                        pass_filter={ 'INDIRECT', 'DIRECT' },
                        margin=margin_pixels,
                        margin_type='ADJACENT_FACES',
                        use_clear=clear_illumination,
                        uv_layer='BakeUV',
                        use_selected_to_active=False,
                    )

                    clear_illumination = False

                # Reset lighting

                if background is not None:
                    background.inputs['Strength'].default_value = world_strength

                for obj, hidden in light_hide_render.items():
                    obj.hide_render = hidden

            # Bake emission

            set_bake_target(emission_image)

            for obj in atlas_objects:
                obj.hide_render = False
            bpy.ops.object.select_all(action='DESELECT')
            for obj in atlas_objects:
                obj.select_set(True)
            bpy.context.view_layer.objects.active = atlas_objects[0]

            scene.cycles.samples = 1

            bpy.ops.object.bake(
                type='EMIT',
                margin=margin_pixels,
                margin_type='ADJACENT_FACES',
                use_clear=True,
                uv_layer='BakeUV',
                use_selected_to_active=False,
            )

        if progress is not None:
            progress(float(atlas_index + 5.0 / 6.0) / atlas_count)

        if bake_ao:
            # Bake global ambient occlusion

            set_bake_target(ao_image)

            scene.cycles.samples = ao_samples

            clear_ao = True

            if static_objects:
                for obj in mesh_objects:
                    obj.hide_render = obj in global_dynamic_objects
                bpy.ops.object.select_all(action='DESELECT')
                for obj in static_objects:
                    obj.select_set(True)
                bpy.context.view_layer.objects.active = static_objects[0]

                bpy.ops.object.bake(
                    type='AO',
                    margin=margin_pixels,
                    margin_type='ADJACENT_FACES',
                    use_clear=clear_ao,
                    uv_layer='BakeUV',
                    use_selected_to_active=False,
                )
                clear_ao = False

            # Bake local ambient occlusion

            for dynamic_object in dynamic_objects:
                for obj in mesh_objects:
                    obj.hide_render = obj != dynamic_object
                bpy.ops.object.select_all(action='DESELECT')
                dynamic_object.select_set(True)
                bpy.context.view_layer.objects.active = dynamic_object

                bpy.ops.object.bake(
                    type='AO',
                    margin=margin_pixels,
                    margin_type='ADJACENT_FACES',
                    use_clear=clear_ao,
                    uv_layer='BakeUV',
                    use_selected_to_active=False,
                )
                clear_ao = False

        if progress is not None:
            progress(float(atlas_index + 1.0) / atlas_count)

        # Remove temporary image target nodes

        for material, target in target_nodes.items():
            material.node_tree.nodes.remove(target)

        # Combine color and ambient occlusion

        if bake_ao:
            color_pixels = np.empty(len(color_image.pixels), dtype=np.float32)
            color_image.pixels.foreach_get(color_pixels)
            color_pixels = color_pixels.reshape(-1, 4)

            ao_pixels = np.empty(len(color_image.pixels), dtype=np.float32)
            ao_image.pixels.foreach_get(ao_pixels)
            ao_pixels = ao_pixels.reshape(-1, 4)

            ao_factor = np.maximum(0.0, 1.0 + (ao_pixels[:, 0] - 1.0) * ao_strength)

            color_pixels[:, :3] *= ao_factor[:, None]

            color_image.pixels.foreach_set(color_pixels.ravel())
            color_image.update()

            del color_pixels
            del ao_pixels

            bpy.data.images.remove(ao_image)

        # Combine emission and illumination

        if bake_emission:
            emission_pixels = np.empty(len(color_image.pixels), dtype=np.float32)
            emission_image.pixels.foreach_get(emission_pixels)
            emission_pixels = emission_pixels.reshape(-1, 4)

            if bake_emission_illumination:
                illumination_pixels = np.empty(len(color_image.pixels), dtype=np.float32)
                illumination_image.pixels.foreach_get(illumination_pixels)
                illumination_pixels = illumination_pixels.reshape(-1, 4)

                emission_pixels[:, :3] += illumination_pixels[:, :3] * illumination_strength

                bpy.data.images.remove(illumination_image)

                del illumination_pixels

            emission_image.pixels.foreach_set(emission_pixels.ravel())
            emission_image.update()

            del emission_pixels

        # Create the baked material

        baked_material = bpy.data.materials.new(name=f'BakedDiffuse{atlas_index}')
        baked_material.use_nodes = True

        materials.append(baked_material)

        nodes = baked_material.node_tree.nodes
        links = baked_material.node_tree.links

        nodes.clear()

        output = nodes.new('ShaderNodeOutputMaterial')
        principled = nodes.new('ShaderNodeBsdfPrincipled')
        uv_map = nodes.new('ShaderNodeUVMap')

        texture = nodes.new('ShaderNodeTexImage')
        if use_nearest_filtering:
            texture.interpolation = 'Closest'
        texture.image = color_image
        uv_map.uv_map = 'BakeUV'

        principled.inputs['Metallic'].default_value = 0.0
        principled.inputs['Roughness'].default_value = 1.0

        links.new(uv_map.outputs['UV'], texture.inputs['Vector'])
        links.new(texture.outputs['Color'], principled.inputs['Base Color'])
        links.new(principled.outputs['BSDF'], output.inputs['Surface'])

        if bake_normal:
            normal_texture = nodes.new('ShaderNodeTexImage')
            if use_nearest_filtering:
                normal_texture.interpolation = 'Closest'
            normal_texture.image = normal_image

            normal_map = nodes.new('ShaderNodeNormalMap')
            normal_map.space = 'TANGENT'
            normal_map.uv_map = 'BakeUV'

            links.new(uv_map.outputs['UV'], normal_texture.inputs['Vector'])
            links.new(normal_texture.outputs['Color'], normal_map.inputs['Color'])
            links.new(normal_map.outputs['Normal'], principled.inputs['Normal'])

        if bake_emission:
            emission_texture = nodes.new('ShaderNodeTexImage')
            if use_nearest_filtering:
                emission_texture.interpolation = 'Closest'
            emission_texture.image = emission_image

            links.new(uv_map.outputs['UV'], emission_texture.inputs['Vector'])
            links.new(emission_texture.outputs['Color'], principled.inputs['Emission Color'])
            principled.inputs['Emission Strength'].default_value = 1.0

        if bake_roughness:
            roughness_texture = nodes.new('ShaderNodeTexImage')
            if use_nearest_filtering:
                roughness_texture.interpolation = 'Closest'
            roughness_texture.image = roughness_image

            links.new(uv_map.outputs['UV'], roughness_texture.inputs['Vector'])
            links.new(roughness_texture.outputs['Color'], principled.inputs['Roughness'])

        print(f'Baked {len(atlas_objects)} objects to atlas index {atlas_index} ({atlas_size}x{atlas_size})')

    # Clear old materials and append the new material

    for obj in mesh_objects:
        atlas_index = obj.get('atlas_index', -1)
        if atlas_index == -1:
            continue

        # Make the BakeUV the real UV

        bake_uv = obj.data.uv_layers.get('BakeUV')

        for uv_layer in list(obj.data.uv_layers):
            if uv_layer != bake_uv:
                obj.data.uv_layers.remove(uv_layer)

        bake_uv.active = True
        bake_uv.active_render = True

        # Replace the material

        material = materials[atlas_index]

        obj.data.materials.clear()
        obj.data.materials.append(material)

        for polygon in obj.data.polygons:
            polygon.material_index = 0

    for obj in mesh_objects:
        obj.hide_render = False

    for obj, hidden in external_hide_render.items():
        obj.hide_render = hidden

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

    desired_texel_world_size: bpy.props.FloatProperty(
        name='Desired Texel World Size (meters per pixel)',
        default=0.005,
        min=1e-6,
        precision=3,
    )

    use_nearest_filtering: bpy.props.BoolProperty(
        name='Use Nearest Texture Filtering',
        default=False,
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
        name='Bake Lights (disable for ingame lighting)',
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
        default=1024,
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
        info_box.label(text='Add a Boolean property "no_bake" as true to skip bake.')

        layout.separator()

        layout.prop(self, 'collection')

        layout.separator()

        layout.prop(self, 'atlas_size')
        layout.prop(self, 'desired_texel_world_size')
        layout.prop(self, 'use_nearest_filtering')

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

        ensure_packages()

        wm = context.window_manager
        wm.progress_begin(0, 100)
        wm.progress_update(0)

        def update_progress(value):
            wm.progress_update(int(value * 100))

        bake(
            objects,
            atlas_size=self.atlas_size,
            desired_texel_world_size=self.desired_texel_world_size,
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
            use_nearest_filtering=self.use_nearest_filtering,
            progress=update_progress,
        )

        wm.progress_end()

        for window in wm.windows:
            for area in window.screen.areas:
                area.tag_redraw()

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
