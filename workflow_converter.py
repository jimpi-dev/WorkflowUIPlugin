import json
import logging
from typing import Dict, Any, List, Tuple, Optional, Union
logger = logging.getLogger(__name__)
try:
    import nodes
except ImportError as e:
    raise ImportError('Cannot import ComfyUI nodes module. This converter must be run within the ComfyUI environment. Make sure ComfyUI is properly initialized before using the converter.') from e
_node_info_cache = {}

def _strip_nulls(s: str) -> str:
    if not isinstance(s, str):
        return s
    return s.replace('\x00', '').replace('\x00', '')

def get_node_info_for_type(node_type: str) -> Dict[str, Any]:
    global _node_info_cache
    node_type = _strip_nulls(node_type)
    if node_type not in _node_info_cache:
        if node_type in nodes.NODE_CLASS_MAPPINGS:
            try:
                obj_class = nodes.NODE_CLASS_MAPPINGS[node_type]
                info = {}
                info['input'] = obj_class.INPUT_TYPES()
                info['input_order'] = {key: list(value.keys()) for key, value in obj_class.INPUT_TYPES().items()}
                _node_info_cache[node_type] = info
            except ValueError as e:
                if 'null bytes' in str(e).lower():
                    logger.error("WorkflowConverter: node type '%s' triggered 'source code string cannot contain null bytes'. Treating as unknown (using fallback input order). Check this custom node's source or ComfyUI.", node_type, exc_info=True)
                    _node_info_cache[node_type] = None
                    return None
                raise
            except Exception as e:
                logger.debug('Could not get node info for %s: %s', node_type, e)
                _node_info_cache[node_type] = None
        else:
            _node_info_cache[node_type] = None
    return _node_info_cache.get(node_type)

class WorkflowConverter:

    @staticmethod
    def is_subgraph_uuid(node_type: str) -> bool:
        if not node_type or not isinstance(node_type, str):
            return False
        if len(node_type) == 36 and node_type.count('-') == 4:
            parts = node_type.split('-')
            if len(parts) == 5 and all((len(p) in [8, 4, 4, 4, 12] for i, p in enumerate(parts) if i == 0 or i == 4 or len(p) == 4)):
                return True
        return False

    @staticmethod
    def expand_subgraph(subgraph_node_id: int, subgraph_def: Dict[str, Any], workflow_links: List) -> Tuple[List[Dict], List]:
        expanded_nodes = []
        expanded_links = []
        internal_nodes = subgraph_def.get('nodes', [])
        internal_links = subgraph_def.get('links', [])
        max_link_id = 0
        for link in workflow_links:
            if isinstance(link, (list, tuple)) and len(link) > 0:
                link_id = link[0]
                if isinstance(link_id, int) and link_id > max_link_id:
                    max_link_id = link_id
        link_id_remap = {}
        next_link_id = max_link_id + 1
        for link in internal_links:
            if isinstance(link, dict):
                old_id = link.get('id')
                if old_id is not None:
                    link_id_remap[old_id] = next_link_id
                    next_link_id += 1
        internal_link_map = {}
        for link in internal_links:
            if isinstance(link, dict):
                link_id = link.get('id')
                internal_link_map[link_id] = link
        subgraph_inputs = subgraph_def.get('inputs', [])
        subgraph_outputs = subgraph_def.get('outputs', [])
        input_slot_to_internal = {}
        internal_to_output_slot = {}
        for idx, input_def in enumerate(subgraph_inputs):
            input_link_ids = input_def.get('linkIds', [])
            targets = []
            for link_id in input_link_ids:
                if link_id in internal_link_map:
                    link = internal_link_map[link_id]
                    target_id = link.get('target_id')
                    target_slot = link.get('target_slot')
                    targets.append((target_id, target_slot))
            if targets:
                input_slot_to_internal[idx] = targets
        for idx, output_def in enumerate(subgraph_outputs):
            output_link_ids = output_def.get('linkIds', [])
            for link_id in output_link_ids:
                if link_id in internal_link_map:
                    link = internal_link_map[link_id]
                    origin_id = link.get('origin_id')
                    origin_slot = link.get('origin_slot')
                    internal_to_output_slot[origin_id, origin_slot] = idx
        for node in internal_nodes:
            internal_id = node.get('id')
            expanded_node = node.copy()
            expanded_node['id'] = f'{subgraph_node_id}:{internal_id}'
            if 'inputs' in expanded_node:
                updated_inputs = []
                for input_info in expanded_node['inputs']:
                    input_link = input_info.get('link')
                    if input_link in internal_link_map:
                        link_data = internal_link_map[input_link]
                        if link_data.get('origin_id') == -10:
                            input_copy = input_info.copy()
                            input_copy['link'] = None
                            updated_inputs.append(input_copy)
                        else:
                            input_copy = input_info.copy()
                            if input_link in link_id_remap:
                                input_copy['link'] = link_id_remap[input_link]
                            updated_inputs.append(input_copy)
                    else:
                        updated_inputs.append(input_info)
                expanded_node['inputs'] = updated_inputs
            expanded_nodes.append(expanded_node)
        for link in internal_links:
            if isinstance(link, dict):
                origin_id = link.get('origin_id')
                target_id = link.get('target_id')
                if origin_id in [-10, -20] or target_id in [-10, -20]:
                    continue
                old_link_id = link.get('id')
                new_link_id = link_id_remap.get(old_link_id, old_link_id)
                expanded_link = [new_link_id, f'{subgraph_node_id}:{origin_id}', link.get('origin_slot'), f'{subgraph_node_id}:{target_id}', link.get('target_slot'), link.get('type')]
                expanded_links.append(expanded_link)
        return (expanded_nodes, expanded_links, input_slot_to_internal, internal_to_output_slot)

    @staticmethod
    def is_api_format(workflow: Dict[str, Any]) -> bool:
        if 'nodes' in workflow and 'links' in workflow:
            return False
        for key, value in workflow.items():
            if key in ['prompt', 'extra_data', 'client_id']:
                continue
            if isinstance(value, dict) and 'class_type' in value:
                return True
        return False

    @staticmethod
    def _strip_null_bytes(obj: Any) -> Any:
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                key = k.replace('\x00', '').replace('\x00', '') if isinstance(k, str) else k
                out[key] = WorkflowConverter._strip_null_bytes(v)
            return out
        if isinstance(obj, list):
            return [WorkflowConverter._strip_null_bytes(v) for v in obj]
        if isinstance(obj, str):
            return obj.replace('\x00', '').replace('\x00', '')
        return obj

    @staticmethod
    def convert_to_api(workflow: Dict[str, Any]) -> Dict[str, Any]:
        workflow = WorkflowConverter._strip_null_bytes(workflow)
        if WorkflowConverter.is_api_format(workflow):
            return workflow
        workflow_nodes = workflow.get('nodes', [])
        links = workflow.get('links', [])
        node_types_in_workflow = sorted(set((n.get('type') or n.get('class_type') for n in workflow_nodes if isinstance(n, dict) and (n.get('type') or n.get('class_type')))))
        logger.info('WorkflowConverter: starting conversion, %s nodes, %s links. Node types: %s', len(workflow_nodes), len(links), node_types_in_workflow[:40])
        subgraph_defs = {}
        definitions = workflow.get('definitions', {})
        for subgraph in definitions.get('subgraphs', []):
            subgraph_id = subgraph.get('id')
            if subgraph_id:
                subgraph_defs[subgraph_id] = subgraph
        subgraph_input_mappings = {}
        subgraph_output_mappings = {}
        subgraph_slot_to_input_idx = {}
        max_iterations = 10
        iteration = 0
        while iteration < max_iterations:
            iteration += 1
            expanded_nodes = []
            found_subgraph = False
            for node in workflow_nodes:
                node_type = _strip_nulls(node.get('type') or '')
                node_id = node.get('id')
                if WorkflowConverter.is_subgraph_uuid(node_type) and node_type in subgraph_defs:
                    found_subgraph = True
                    logger.debug(f'Expanding subgraph node {node_id} (type: {node_type}) - iteration {iteration}')
                    subgraph_def = subgraph_defs[node_type]
                    sg_nodes, sg_links, input_map, output_map = WorkflowConverter.expand_subgraph(node_id, subgraph_def, links)
                    expanded_nodes.extend(sg_nodes)
                    links.extend(sg_links)
                    subgraph_input_mappings[str(node_id)] = input_map
                    subgraph_output_mappings[str(node_id)] = output_map
                    outer_inputs = node.get('inputs', [])
                    subgraph_inputs = subgraph_def.get('inputs', [])
                    subgraph_input_name_to_idx = {}
                    for idx, sg_input in enumerate(subgraph_inputs):
                        input_name = sg_input.get('name')
                        if input_name:
                            subgraph_input_name_to_idx[input_name] = idx
                    slot_mapping = {}
                    for outer_slot, outer_input in enumerate(outer_inputs):
                        outer_name = outer_input.get('name')
                        if outer_name and outer_name in subgraph_input_name_to_idx:
                            slot_mapping[outer_slot] = subgraph_input_name_to_idx[outer_name]
                            logger.debug(f"Subgraph {node_id}: outer slot {outer_slot} ('{outer_name}') -> subgraph input idx {subgraph_input_name_to_idx[outer_name]}")
                    subgraph_slot_to_input_idx[str(node_id)] = slot_mapping
                else:
                    expanded_nodes.append(node)
            workflow_nodes = expanded_nodes
            if not found_subgraph:
                logger.debug(f'Subgraph expansion complete after {iteration} iteration(s)')
                break
        if iteration >= max_iterations:
            logger.warning(f'Reached maximum subgraph expansion iterations ({max_iterations}). There may be circular subgraph references.')

        def resolve_subgraph_output(node_id_str, slot, depth=0):
            if depth > 100:
                logger.warning(f'Max recursion depth reached resolving subgraph output for node {node_id_str}')
                return (node_id_str, slot)
            if node_id_str in subgraph_output_mappings:
                output_map = subgraph_output_mappings[node_id_str]
                for (internal_node, internal_slot), out_slot in output_map.items():
                    if out_slot == slot:
                        new_node_id = f'{node_id_str}:{internal_node}'
                        return resolve_subgraph_output(new_node_id, internal_slot, depth + 1)
            return (node_id_str, slot)

        def resolve_subgraph_input(node_id_str, slot, depth=0):
            if depth > 100:
                logger.warning(f'Max recursion depth reached resolving subgraph input for node {node_id_str}')
                return (node_id_str, slot)
            if node_id_str in subgraph_input_mappings:
                input_map = subgraph_input_mappings[node_id_str]
                subgraph_input_idx = slot
                if node_id_str in subgraph_slot_to_input_idx:
                    slot_mapping = subgraph_slot_to_input_idx[node_id_str]
                    if slot in slot_mapping:
                        subgraph_input_idx = slot_mapping[slot]
                if subgraph_input_idx in input_map:
                    targets = input_map[subgraph_input_idx]
                    if targets:
                        internal_node, internal_slot = targets[0]
                        new_node_id = f'{node_id_str}:{internal_node}'
                        return resolve_subgraph_input(new_node_id, internal_slot, depth + 1)
            return (node_id_str, slot)

        def resolve_subgraph_input_all(node_id_str, slot, depth=0):
            if depth > 100:
                logger.warning(f'Max recursion depth reached resolving subgraph input for node {node_id_str}')
                return [(node_id_str, slot)]
            if node_id_str in subgraph_input_mappings:
                input_map = subgraph_input_mappings[node_id_str]
                subgraph_input_idx = slot
                if node_id_str in subgraph_slot_to_input_idx:
                    slot_mapping = subgraph_slot_to_input_idx[node_id_str]
                    if slot in slot_mapping:
                        subgraph_input_idx = slot_mapping[slot]
                if subgraph_input_idx in input_map:
                    targets = input_map[subgraph_input_idx]
                    results = []
                    for internal_node, internal_slot in targets:
                        new_node_id = f'{node_id_str}:{internal_node}'
                        results.extend(resolve_subgraph_input_all(new_node_id, internal_slot, depth + 1))
                    return results if results else [(node_id_str, slot)]
            return [(node_id_str, slot)]
        node_input_updates = {}
        updated_links = []
        for link in links:
            if len(link) >= 6:
                link_id = link[0]
                source_id = link[1]
                source_slot = link[2]
                target_id = link[3]
                target_slot = link[4]
                link_type = link[5] if len(link) > 5 else None
                source_id_str = str(source_id)
                source_id, source_slot = resolve_subgraph_output(source_id_str, source_slot)
                target_id_str = str(target_id)
                all_targets = resolve_subgraph_input_all(target_id_str, target_slot)
                for resolved_target_id, resolved_target_slot in all_targets:
                    if resolved_target_id != target_id_str:
                        if resolved_target_id not in node_input_updates:
                            node_input_updates[resolved_target_id] = {}
                        node_input_updates[resolved_target_id][resolved_target_slot] = link_id
                resolved_target_id, resolved_target_slot = all_targets[0]
                target_id = resolved_target_id
                target_slot = resolved_target_slot
                updated_links.append([link_id, source_id, source_slot, target_id, target_slot, link_type])
        links = updated_links
        for node in workflow_nodes:
            node_id_str = str(node.get('id'))
            if node_id_str in node_input_updates and 'inputs' in node:
                slot_to_link = node_input_updates[node_id_str]
                inputs = node.get('inputs', [])
                for i, input_info in enumerate(inputs):
                    if i in slot_to_link:
                        input_info['link'] = slot_to_link[i]
        link_map = {}
        nodes_with_connected_outputs = set()
        for link in links:
            if len(link) >= 6:
                link_id = link[0]
                source_id = link[1]
                source_slot = link[2]
                target_id = link[3]
                target_slot = link[4]
                link_type = link[5] if len(link) > 5 else None
                link_map[link_id] = {'source_id': source_id, 'source_slot': source_slot, 'target_id': target_id, 'target_slot': target_slot, 'type': link_type}
                nodes_with_connected_outputs.add(source_id)
        primitive_values = {}
        nodes_to_exclude = set()
        bypassed_nodes = set()
        set_node_sources = {}
        get_node_vars = {}
        node_by_id = {}
        for node in workflow_nodes:
            node_by_id[str(node.get('id'))] = node
        for node in workflow_nodes:
            node_id = node.get('id')
            node_type = _strip_nulls(node.get('type') or '')
            node_mode = node.get('mode', 0)
            if node_mode == 4:
                bypassed_nodes.add(str(node_id))
                logger.debug(f'Tracking bypassed node {node_id} ({node_type})')
            if node_type == 'PrimitiveNode':
                node_id_str = str(node_id)
                widget_values = node.get('widgets_values')
                if widget_values and len(widget_values) > 0:
                    primitive_values[node_id_str] = widget_values[0]
            elif node_type == 'SetNode':
                widget_values = node.get('widgets_values')
                if widget_values and len(widget_values) > 0:
                    var_name = widget_values[0]
                    node_inputs = node.get('inputs', [])
                    if node_inputs:
                        for input_info in node_inputs:
                            input_link = input_info.get('link')
                            if input_link is not None and input_link in link_map:
                                link_data = link_map[input_link]
                                set_node_sources[var_name] = (link_data['source_id'], link_data['source_slot'])
                                logger.debug(f"SetNode '{var_name}' receives from node {link_data['source_id']} slot {link_data['source_slot']}")
                                break
            elif node_type == 'GetNode':
                widget_values = node.get('widgets_values')
                if widget_values and len(widget_values) > 0:
                    var_name = widget_values[0]
                    get_node_vars[str(node_id)] = var_name
                    logger.debug(f"GetNode {node_id} provides variable '{var_name}'")
            outputs = node.get('outputs', [])
            has_connected_output = False
            for output in outputs:
                output_links = output.get('links', [])
                if output_links and len(output_links) > 0:
                    has_connected_output = True
                    break
            if node_type == 'LoadImageOutput':
                nodes_to_exclude.add(str(node_id))
                logger.debug(f'Marking node {node_id} ({node_type}) for exclusion - UI-only node type')
            elif not outputs or not has_connected_output:
                node_class = nodes.NODE_CLASS_MAPPINGS.get(node_type) if hasattr(nodes, 'NODE_CLASS_MAPPINGS') else None
                is_output_node = node_class and hasattr(node_class, 'OUTPUT_NODE') and node_class.OUTPUT_NODE
                has_connected_input = False
                if not is_output_node and (not node_class):
                    node_inputs = node.get('inputs', [])
                    for input_info in node_inputs:
                        if input_info.get('link') is not None:
                            has_connected_input = True
                            break
                if not is_output_node and (not has_connected_input):
                    nodes_to_exclude.add(str(node_id))
                    logger.debug(f'Marking node {node_id} ({node_type}) for exclusion - no connected outputs')
                else:
                    logger.debug(f'Keeping node {node_id} ({node_type}) - OUTPUT_NODE=True or has connected inputs despite no connected outputs')

        def trace_through_get_set_nodes(source_node_id, source_slot, visited=None):
            if visited is None:
                visited = set()
            source_node_id_str = str(source_node_id)
            if source_node_id_str in visited:
                return (source_node_id, source_slot)
            visited.add(source_node_id_str)
            if source_node_id_str in get_node_vars:
                var_name = get_node_vars[source_node_id_str]
                if var_name in set_node_sources:
                    actual_source_id, actual_source_slot = set_node_sources[var_name]
                    logger.debug(f"Tracing GetNode {source_node_id} (var '{var_name}') -> actual source {actual_source_id} slot {actual_source_slot}")
                    return trace_through_get_set_nodes(actual_source_id, actual_source_slot, visited)
                else:
                    logger.warning(f"GetNode {source_node_id} references variable '{var_name}' but no SetNode found for it")
            return (source_node_id, source_slot)

        def trace_through_bypassed(source_node_id, source_slot, visited=None):
            if visited is None:
                visited = set()
            if source_node_id in visited:
                return (source_node_id, source_slot)
            visited.add(source_node_id)
            if source_node_id not in bypassed_nodes:
                return (source_node_id, source_slot)
            for node in workflow_nodes:
                if str(node.get('id')) == str(source_node_id):
                    node_type = node.get('type', 'unknown')
                    node_inputs = node.get('inputs', [])
                    node_outputs = node.get('outputs', [])
                    output_type = None
                    if node_outputs and source_slot < len(node_outputs):
                        output_type = node_outputs[source_slot].get('type')
                    if node_inputs:
                        linked_input = None
                        fallback_linked_input = None
                        for idx, input_info in enumerate(node_inputs):
                            input_link = input_info.get('link')
                            input_type = input_info.get('type')
                            if input_link is not None and input_link in link_map:
                                if fallback_linked_input is None:
                                    fallback_linked_input = input_link
                                if output_type and input_type == output_type:
                                    linked_input = input_link
                                    break
                        if linked_input is None:
                            linked_input = fallback_linked_input
                        if linked_input is not None:
                            link_data = link_map[linked_input]
                            upstream_source_id = link_data['source_id']
                            upstream_source_slot = link_data['source_slot']
                            upstream_source_id, upstream_source_slot = trace_through_get_set_nodes(upstream_source_id, upstream_source_slot)
                            return trace_through_bypassed(upstream_source_id, upstream_source_slot, visited)
                    break
            return (source_node_id, source_slot)
        api_prompt = {}
        for node in workflow_nodes:
            node_id = str(node.get('id'))
            node_type = _strip_nulls(node.get('type') or '')
            if not node_type:
                continue
            node_mode = node.get('mode', 0)
            if node_mode == 2:
                logger.debug(f'Skipping muted node {node_id} ({node_type})')
                continue
            elif node_mode == 4:
                logger.debug(f'Skipping bypassed/disabled node {node_id} ({node_type})')
                continue
            if node_type in ['Note', 'PrimitiveNode', 'GetNode', 'SetNode']:
                logger.debug(f'Skipping {node_type} node {node_id}')
                continue
            if node_id in nodes_to_exclude:
                logger.debug(f'Skipping {node_type} node {node_id} - no connected outputs')
                continue
            api_node = {'inputs': {}, 'class_type': node_type}
            if 'title' in node:
                api_node['_meta'] = {'title': node['title']}
            elif hasattr(nodes, 'NODE_DISPLAY_NAME_MAPPINGS') and node_type in nodes.NODE_DISPLAY_NAME_MAPPINGS:
                api_node['_meta'] = {'title': nodes.NODE_DISPLAY_NAME_MAPPINGS[node_type]}
            else:
                api_node['_meta'] = {'title': node_type}
            link_inputs = {}
            primitive_inputs = {}
            node_inputs = node.get('inputs', [])
            if node_inputs:
                for input_info in node_inputs:
                    input_name = input_info.get('name')
                    input_link = input_info.get('link')
                    if input_link is not None and input_link in link_map:
                        link_data = link_map[input_link]
                        source_node_id = link_data['source_id']
                        source_slot = link_data['source_slot']
                        actual_source_id, actual_source_slot = trace_through_get_set_nodes(source_node_id, source_slot)
                        if str(actual_source_id) in bypassed_nodes:
                            actual_source_id, actual_source_slot = trace_through_bypassed(actual_source_id, actual_source_slot)
                            if str(actual_source_id) in bypassed_nodes:
                                logger.debug(f'Could not trace through bypassed node {source_node_id} for input {input_name}, falling back to widget value')
                                continue
                        actual_source_id, actual_source_slot = trace_through_get_set_nodes(actual_source_id, actual_source_slot)
                        actual_source_id, actual_source_slot = resolve_subgraph_output(str(actual_source_id), actual_source_slot)
                        source_node_id_str = str(actual_source_id)
                        if source_node_id_str in primitive_values:
                            primitive_inputs[input_name] = primitive_values[source_node_id_str]
                        elif actual_source_id in nodes_to_exclude:
                            logger.debug(f'Skipping input {input_name} from excluded node {source_node_id_str}')
                        elif actual_source_id in bypassed_nodes:
                            logger.warning(f'Could not resolve bypassed node {source_node_id} for input {input_name}, skipping connection')
                        else:
                            if actual_source_id != source_node_id:
                                logger.info(f'Bypassed disabled node {source_node_id}, connecting {input_name} to {actual_source_id} (slot {actual_source_slot}) instead')
                            link_inputs[input_name] = [str(actual_source_id), actual_source_slot]
            logger.info('WorkflowConverter: processing node id=%s type=%s', node_id, node_type)
            ordered_inputs = WorkflowConverter._get_ordered_inputs(node_type, node)
            widget_values = node.get('widgets_values')
            widget_inputs = {}
            if widget_values is not None:
                if isinstance(widget_values, dict):
                    for key, value in widget_values.items():
                        if key in ['videopreview', 'preview']:
                            continue
                        if key not in link_inputs:
                            widget_inputs[key] = value
                elif isinstance(widget_values, list):
                    has_dict_widgets = any((isinstance(v, dict) for v in widget_values))
                    if has_dict_widgets:
                        WorkflowConverter._process_dict_widget_values(widget_values, widget_inputs, link_inputs)
                    else:
                        widget_mappings = WorkflowConverter._get_widget_mappings(node_type, node)
                        filtered_values = WorkflowConverter._filter_control_values(widget_values)
                        if widget_mappings:
                            for i, value in enumerate(filtered_values):
                                if i < len(widget_mappings):
                                    widget_name = widget_mappings[i]
                                    if widget_name and widget_name not in link_inputs:
                                        widget_inputs[widget_name] = value
                        elif filtered_values:
                            logger.warning(f"Could not map widget values for unknown node type '{node_type}' (node {node_id})")
            default_inputs = WorkflowConverter._get_default_inputs(node_type, widget_inputs, primitive_inputs, link_inputs)
            if ordered_inputs:
                for input_name in ordered_inputs:
                    if input_name in widget_inputs:
                        api_node['inputs'][input_name] = widget_inputs[input_name]
                    elif input_name in primitive_inputs:
                        api_node['inputs'][input_name] = primitive_inputs[input_name]
                    elif input_name in default_inputs:
                        api_node['inputs'][input_name] = default_inputs[input_name]
                for input_name in ordered_inputs:
                    if input_name in link_inputs and input_name not in api_node['inputs']:
                        api_node['inputs'][input_name] = link_inputs[input_name]
                for key, value in widget_inputs.items():
                    if key not in api_node['inputs']:
                        api_node['inputs'][key] = value
                for key, value in primitive_inputs.items():
                    if key not in api_node['inputs']:
                        api_node['inputs'][key] = value
                for key, value in default_inputs.items():
                    if key not in api_node['inputs']:
                        api_node['inputs'][key] = value
                for key, value in link_inputs.items():
                    if key not in api_node['inputs']:
                        api_node['inputs'][key] = value
            else:
                for key, value in widget_inputs.items():
                    api_node['inputs'][key] = value
                for key, value in primitive_inputs.items():
                    if key not in api_node['inputs']:
                        api_node['inputs'][key] = value
                for key, value in default_inputs.items():
                    if key not in api_node['inputs']:
                        api_node['inputs'][key] = value
                for key, value in link_inputs.items():
                    if key not in api_node['inputs']:
                        api_node['inputs'][key] = value
            api_prompt[node_id] = api_node
        return api_prompt

    @staticmethod
    def _process_dict_widget_values(widget_values: List[Any], widget_inputs: Dict[str, Any], link_inputs: Dict[str, Any]) -> None:
        lora_counter = 0
        for value in widget_values:
            if isinstance(value, dict):
                if not value:
                    continue
                elif 'type' in value:
                    widget_name = value.get('type')
                    if widget_name and widget_name not in link_inputs:
                        widget_inputs[widget_name] = value
                elif 'lora' in value:
                    lora_counter += 1
                    widget_name = f'lora_{lora_counter}'
                    if widget_name not in link_inputs:
                        clean_value = {k: v for k, v in value.items() if k != 'strengthTwo' or v is not None}
                        widget_inputs[widget_name] = clean_value
                else:
                    logger.debug(f'Unknown dict widget value structure: {value}')
            elif isinstance(value, str):
                if value == '':
                    widget_inputs['➕ Add Lora'] = value

    @staticmethod
    def _filter_control_values(widget_values: List[Any]) -> List[Any]:
        filtered = []
        skip_next = False
        for i, value in enumerate(widget_values):
            if skip_next:
                skip_next = False
                continue
            if value in ['fixed', 'increment', 'decrement', 'randomize']:
                continue
            if i + 1 < len(widget_values):
                next_val = widget_values[i + 1]
                if next_val in ['fixed', 'increment', 'decrement', 'randomize']:
                    filtered.append(value)
                    continue
            filtered.append(value)
        return filtered

    @staticmethod
    def _get_ordered_inputs(node_type: str, node: Dict[str, Any]) -> List[str]:
        node_type = _strip_nulls(node_type)
        properties = node.get('properties', {})
        if 'Node name for S&R' in properties:
            node_type = _strip_nulls(properties['Node name for S&R'])
        node_info = get_node_info_for_type(node_type)
        if node_info and 'input_order' in node_info:
            input_order = node_info['input_order']
            input_names = []
            for section in ['required', 'optional']:
                if section in input_order:
                    input_names.extend(input_order[section])
            if input_names:
                return input_names
        if hasattr(nodes, 'NODE_CLASS_MAPPINGS') and node_type in nodes.NODE_CLASS_MAPPINGS:
            try:
                node_class = nodes.NODE_CLASS_MAPPINGS[node_type]
                input_types = node_class.INPUT_TYPES()
                input_names = []
                for section in ['required', 'optional']:
                    if section in input_types:
                        for input_name in input_types[section].keys():
                            input_names.append(input_name)
                if input_names:
                    return input_names
            except ValueError as e:
                if 'null bytes' in str(e).lower():
                    logger.warning("WorkflowConverter: node type '%s' raised null bytes in INPUT_TYPES(); using fallback order", node_type)
                return []
            except Exception as e:
                logger.debug('Could not get input order from node class for %s: %s', node_type, e)
        return []

    @staticmethod
    def _get_dynamic_combo_sub_inputs(input_name: str, input_spec: Any, widget_values: List[Any], current_idx: int) -> List[str]:
        if not isinstance(input_spec, (list, tuple)) or len(input_spec) < 2:
            return []
        input_type = input_spec[0]
        if not isinstance(input_type, str) or not input_type.startswith('COMFY_') or 'COMBO' not in input_type:
            return []
        spec_options = input_spec[1] if len(input_spec) > 1 else {}
        if not isinstance(spec_options, dict):
            return []
        options = spec_options.get('options', [])
        if not options or current_idx >= len(widget_values):
            return []
        selected_value = widget_values[current_idx]
        for option in options:
            if isinstance(option, dict) and option.get('key') == selected_value:
                sub_inputs = option.get('inputs', {})
                sub_input_names = []
                for section in ['required', 'optional']:
                    if section in sub_inputs:
                        for sub_name in sub_inputs[section].keys():
                            sub_input_names.append(f'{input_name}.{sub_name}')
                return sub_input_names
        return []

    @staticmethod
    def _get_widget_mappings(node_type: str, node: Dict[str, Any]) -> List[Optional[str]]:
        node_type = _strip_nulls(node_type)
        properties = node.get('properties', {})
        if 'Node name for S&R' in properties:
            node_type = _strip_nulls(properties['Node name for S&R'])
        widget_values = node.get('widgets_values', [])
        if isinstance(widget_values, dict):
            widget_values = []
        node_info = get_node_info_for_type(node_type)
        if node_info and 'input' in node_info:
            try:
                input_def = node_info['input']
                widget_names = []
                widget_idx = 0
                for section in ['required', 'optional']:
                    if section in input_def:
                        for input_name, input_spec in input_def[section].items():
                            if isinstance(input_spec, (list, tuple)) and len(input_spec) >= 1:
                                input_type = input_spec[0]
                                is_widget = False
                                is_dynamic_combo = False
                                if isinstance(input_type, list):
                                    is_widget = True
                                elif input_type in ['INT', 'FLOAT', 'STRING', 'BOOLEAN', 'COMBO']:
                                    is_widget = True
                                elif isinstance(input_type, str) and input_type.startswith('COMFY_') and ('COMBO' in input_type):
                                    is_widget = True
                                    is_dynamic_combo = True
                                elif isinstance(input_type, str) and (not input_type.isupper()):
                                    is_widget = True
                                if is_widget:
                                    widget_names.append(input_name)
                                    if is_dynamic_combo and widget_values:
                                        sub_inputs = WorkflowConverter._get_dynamic_combo_sub_inputs(input_name, input_spec, widget_values, widget_idx)
                                        widget_names.extend(sub_inputs)
                                        widget_idx += 1 + len(sub_inputs)
                                    else:
                                        widget_idx += 1
                if widget_names:
                    return widget_names
            except Exception as e:
                logger.debug(f'Could not get widget mappings from node info for {node_type}: {e}')
        if hasattr(nodes, 'NODE_CLASS_MAPPINGS') and node_type in nodes.NODE_CLASS_MAPPINGS:
            try:
                node_class = nodes.NODE_CLASS_MAPPINGS[node_type]
                input_types = node_class.INPUT_TYPES()
                widget_names = []
                widget_idx = 0
                for section in ['required', 'optional']:
                    if section in input_types:
                        for input_name, input_spec in input_types[section].items():
                            if isinstance(input_spec, tuple) and len(input_spec) >= 1:
                                input_type = input_spec[0]
                                is_widget = False
                                is_dynamic_combo = False
                                if isinstance(input_type, list):
                                    is_widget = True
                                elif input_type in ['INT', 'FLOAT', 'STRING', 'BOOLEAN', 'COMBO']:
                                    is_widget = True
                                elif isinstance(input_type, str) and input_type.startswith('COMFY_') and ('COMBO' in input_type):
                                    is_widget = True
                                    is_dynamic_combo = True
                                elif isinstance(input_type, str) and (not input_type.isupper()):
                                    is_widget = True
                                if is_widget:
                                    widget_names.append(input_name)
                                    if is_dynamic_combo and widget_values:
                                        sub_inputs = WorkflowConverter._get_dynamic_combo_sub_inputs(input_name, input_spec, widget_values, widget_idx)
                                        widget_names.extend(sub_inputs)
                                        widget_idx += 1 + len(sub_inputs)
                                    else:
                                        widget_idx += 1
                if widget_names:
                    return widget_names
            except ValueError as e:
                if 'null bytes' in str(e).lower():
                    logger.warning("WorkflowConverter: node type '%s' raised null bytes in INPUT_TYPES(); using fallback widget order", node_type)
                return []
            except Exception as e:
                logger.debug('Could not get widget mappings from node class for %s: %s', node_type, e)
        widget_values = node.get('widgets_values', [])
        if not isinstance(widget_values, list) or len(widget_values) == 0:
            return []
        properties = node.get('properties', {})
        ue_properties = properties.get('ue_properties', {})
        widget_ue_connectable = ue_properties.get('widget_ue_connectable', {})
        if widget_ue_connectable and isinstance(widget_ue_connectable, dict):
            widget_names = list(widget_ue_connectable.keys())
            if widget_names and len(widget_names) >= len(widget_values):
                return widget_names[:len(widget_values)]
        all_inputs = []
        connected_inputs = set()
        widget_flagged_inputs = []
        for input_info in node.get('inputs', []):
            input_name = input_info.get('name')
            if input_name:
                all_inputs.append(input_name)
                if input_info.get('link') is not None:
                    connected_inputs.add(input_name)
                if input_info.get('widget'):
                    widget_flagged_inputs.append(input_name)
        if widget_flagged_inputs:
            if len(widget_values) > len(widget_flagged_inputs):
                potential_widgets = [inp for inp in all_inputs if inp not in connected_inputs and inp not in widget_flagged_inputs]
                return widget_flagged_inputs + potential_widgets[:len(widget_values) - len(widget_flagged_inputs)]
            return widget_flagged_inputs
        unconnected = [inp for inp in all_inputs if inp not in connected_inputs]
        if unconnected and len(unconnected) >= len(widget_values):
            return unconnected[:len(widget_values)]
        return []

    @staticmethod
    def _get_default_inputs(node_type: str, widget_inputs: Dict[str, Any], primitive_inputs: Dict[str, Any], link_inputs: Dict[str, Any]) -> Dict[str, Any]:
        default_inputs = {}
        node_type = _strip_nulls(node_type)
        if not hasattr(nodes, 'NODE_CLASS_MAPPINGS') or node_type not in nodes.NODE_CLASS_MAPPINGS:
            return default_inputs
        try:
            node_class = nodes.NODE_CLASS_MAPPINGS[node_type]
            input_types = node_class.INPUT_TYPES()
            for section in ['required', 'optional']:
                if section not in input_types:
                    continue
                for input_name, input_spec in input_types[section].items():
                    if input_name in widget_inputs or input_name in primitive_inputs or input_name in link_inputs:
                        continue
                    if isinstance(input_spec, (list, tuple)) and len(input_spec) >= 1:
                        input_type = input_spec[0]
                        spec_options = input_spec[1] if len(input_spec) >= 2 else {}
                        if isinstance(spec_options, dict) and 'default' in spec_options:
                            default_inputs[input_name] = spec_options['default']
                            logger.debug(f"Adding default value for {node_type}.{input_name}: {spec_options['default']}")
                        elif isinstance(input_type, list) and len(input_type) > 0:
                            default_inputs[input_name] = input_type[0]
                            logger.debug(f'Adding implicit combo default for {node_type}.{input_name}: {input_type[0]}')
                        elif input_type == 'COMBO' and isinstance(spec_options, dict) and ('options' in spec_options):
                            options = spec_options['options']
                            if isinstance(options, list) and len(options) > 0:
                                default_inputs[input_name] = options[0]
                                logger.debug(f'Adding implicit COMBO default for {node_type}.{input_name}: {options[0]}')
        except ValueError as e:
            if 'null bytes' in str(e).lower():
                logger.warning("WorkflowConverter: node type '%s' raised null bytes in _get_default_inputs; skipping defaults", node_type)
        except Exception as e:
            logger.debug('Could not get default inputs for %s: %s', node_type, e)
        return default_inputs