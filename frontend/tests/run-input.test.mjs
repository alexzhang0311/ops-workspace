import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/run-input.ts', import.meta.url), 'utf8');
const {outputText} = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022}});
const {testRunInputs} = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);

test('previous parameters are formatted for editing without losing false, zero or nested values', () => {
  const inputs = {number:0,enabled:false,nested:{items:[1,2]},empty:''};
  const prepared = testRunInputs({run_id:'previous',inputs,omitted_secrets:[]}, '[]');
  assert.equal(prepared.json, JSON.stringify(inputs,null,2));
  assert.match(prepared.hint,/已加载/);
});

test('first run starts with an empty JSON object', () => {
  const prepared = testRunInputs({run_id:null,inputs:{},omitted_secrets:[]}, '[]');
  assert.equal(prepared.json,'{}');
  assert.match(prepared.hint,/暂无历史/);
});

test('unsaved secret definitions remove old values and explain omitted parameters', () => {
  const previous = {run_id:'previous',inputs:{new_secret:'hidden',visible:7},omitted_secrets:['old_secret']};
  const prepared = testRunInputs(previous, '[{"name":"new_secret","secret":true}]');
  assert.deepEqual(JSON.parse(prepared.json),{visible:7});
  assert.match(prepared.hint,/old_secret、new_secret/);
  assert.equal(previous.inputs.new_secret,'hidden');
});

test('invalid parameter definitions prevent opening a misleading test form', () => {
  const previous = {run_id:null,inputs:{},omitted_secrets:[]};
  assert.throws(()=>testRunInputs(previous,'{'));
  assert.throws(()=>testRunInputs(previous,'{}'),/参数定义须为 JSON 数组/);
});
