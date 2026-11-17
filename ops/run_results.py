"""Stable application result aggregation, including historical run records."""


def application_final_result(content, states, outputs, status):
    if status != 'success':
        return None
    sources = {edge['source'] for edge in content.get('edges', [])}
    endpoints = {
        node['id']: outputs[node['id']]
        for node in content.get('nodes', [])
        if node['id'] not in sources
        and states.get(node['id']) == 'success'
        and node['id'] in outputs
    }
    if len(endpoints) == 1:
        return next(iter(endpoints.values()))
    return endpoints or None
