import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/run-output.ts', import.meta.url), 'utf8');
const {outputText} = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022}});
const {runOutput} = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);

test('application output includes every node and final_result without name collisions', () => {
  const outputs = {first:{n:1},final_result:{n:2}};
  assert.deepEqual(runOutput({kind:'app',result:{outputs,final_result:{n:2}}}), {outputs,final_result:{n:2}});
});

test('false, zero, empty, and null final outputs retain their actual values', () => {
  for (const value of [false,0,'',[],{},null]) {
    assert.deepEqual(runOutput({kind:'app',result:{outputs:{end:value},final_result:value}}), {outputs:{end:value},final_result:value});
  }
});

test('queued or legacy runs still display the final_result field', () => {
  assert.deepEqual(runOutput({kind:'app',result:{}}), {outputs:{},final_result:null});
  assert.deepEqual(runOutput({kind:'app',result:{value:{end:7}}}), {outputs:{end:7},final_result:null});
});

test('script output remains the script return value', () => {
  assert.deepEqual(runOutput({kind:'script',result:{value:{ok:true},stdout:'log'}}), {ok:true});
});
