import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/workflow.ts', import.meta.url), 'utf8');
const {outputText} = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022}});
const {workflowEdgeId, removeWorkflowElements, applyNodeDrafts} = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
const graph = () => ({timeout: 90, nodes: [{id:'parse',label:'解析',parser:{mode:'json'}},{id:'cond',label:'条件',expected:true},{id:'script',label:'脚本',inputs:{}}], edges:[{source:'parse',target:'cond',when:'true'},{source:'cond',target:'script',when:'false'}]});

test('node and edge removal callbacks compose without restoring a deleted node', () => {
  const original = graph();
  const deletedEdge = workflowEdgeId(original.edges[0]);
  const afterNode = removeWorkflowElements(original, ['parse']);
  const afterEdge = removeWorkflowElements(afterNode, [], [deletedEdge]);
  assert.deepEqual(afterEdge.nodes.map(n=>n.id), ['cond','script']);
  assert.deepEqual(afterEdge.edges, [original.edges[1]]);
  assert.equal(afterEdge.timeout, 90);
  assert.equal(original.nodes.length, 3);
});

test('legacy edges keep their identity and branch conditions after another edge is removed', () => {
  const original = graph();
  const remainingId = workflowEdgeId(original.edges[1]);
  const result = removeWorkflowElements(original, [], [workflowEdgeId(original.edges[0])]);
  assert.equal(workflowEdgeId(result.edges[0]), remainingId);
  assert.equal(result.edges[0].when, 'false');
  assert.equal(result.nodes.length, 3);
});

test('one save commits parser, script mappings and false condition values across nodes', () => {
  const result = applyNodeDrafts(graph(), {
    parse:{parser:'{"mode":"regex","pattern":"n=(?<n>\\\\d+)","fields":[]}'},
    script:{inputs:'{"n":{"path":"$.nodes.parse.n"}}'},
    cond:{expected:'false'},
  });
  assert.equal(result.nodes[0].parser.mode, 'regex');
  assert.deepEqual(result.nodes[2].inputs, {n:{path:'$.nodes.parse.n'}});
  assert.equal(result.nodes[1].expected, false);
  assert.deepEqual(result.edges, graph().edges);
});

test('invalid JSON or wrong object shapes block save with the node name', () => {
  for (const text of ['{', 'null', '[]', '123']) {
    assert.throws(()=>applyNodeDrafts(graph(), {script:{inputs:text}}), /脚本.*参数映射/);
  }
  assert.throws(()=>applyNodeDrafts(graph(), {parse:{parser:'{"fields":{}}'}}), /解析.*解析规则/);
  assert.throws(()=>applyNodeDrafts(graph(), {cond:{expected:'false!'}}), /条件.*期望值/);
});

test('deleting a node also discards its invalid editor draft', () => {
  const remaining = removeWorkflowElements(graph(), ['script']);
  const result = applyNodeDrafts(remaining, {script:{inputs:'{'}});
  assert.deepEqual(result.nodes.map(n=>n.id), ['parse','cond']);
  assert.equal(result.edges.length, 1);
});

test('all nodes and incident edges can be deleted for an empty draft', () => {
  assert.deepEqual(removeWorkflowElements(graph(), ['parse','cond','script']), {timeout:90,nodes:[],edges:[]});
});
