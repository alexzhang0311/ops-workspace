type Data = Record<string, any>;
export type NodeField = 'inputs' | 'parser' | 'expected';
export type NodeDrafts = Record<string, Partial<Record<NodeField, string>>>;

export function workflowEdgeId(edge: Data): string {
  return edge.id || `edge_${edge.source}_${edge.target}_${edge.sourceHandle || ''}_${edge.targetHandle || ''}`;
}

export function removeWorkflowElements(content: Data, nodeIds: string[] = [], edgeIds: string[] = []): Data {
  const removedNodes = new Set(nodeIds), removedEdges = new Set(edgeIds);
  return {
    ...content,
    nodes: content.nodes.filter((node: Data) => !removedNodes.has(node.id)),
    edges: content.edges.filter((edge: Data) => !removedNodes.has(edge.source) && !removedNodes.has(edge.target) && !removedEdges.has(workflowEdgeId(edge))),
  };
}

export function parseNodeField(field: NodeField, text: string): any {
  const value = JSON.parse(text);
  if (field !== 'expected' && (value === null || Array.isArray(value) || typeof value !== 'object')) {
    throw new Error('请填写 JSON 对象');
  }
  if (field === 'parser' && value.fields !== undefined && (!Array.isArray(value.fields) || value.fields.some((item: any) => !item || Array.isArray(item) || typeof item !== 'object'))) {
    throw new Error('输出字段 fields 必须为对象数组');
  }
  return value;
}

export function applyNodeDrafts(content: Data, drafts: NodeDrafts): Data {
  const labels = {inputs: '参数映射', parser: '解析规则', expected: '期望值'};
  return {...content, nodes: content.nodes.map((node: Data) => {
    const next = {...node};
    for (const [field, text] of Object.entries(drafts[node.id] || {})) {
      try { next[field] = parseNodeField(field as NodeField, text!); }
      catch { throw new Error(`节点“${node.label || node.id}”的${labels[field as NodeField]}格式不正确，请修正后保存`); }
    }
    return next;
  })};
}
