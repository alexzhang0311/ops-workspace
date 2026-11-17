import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/run-filters.ts', import.meta.url), 'utf8');
const {outputText} = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022}});
const {runSearchParams,emptyRunFilters} = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);

test('combined filters encode names literally and request the next page sentinel',()=>{
  const query=new URLSearchParams(runSearchParams({...emptyRunFilters,kind:'app',name:' 巡检_% & db ',status:'failed',trigger:'test'},2));
  assert.deepEqual(Object.fromEntries(query),{limit:'101',offset:'200',kind:'app',name:'巡检_% & db',status:'failed',trigger:'test'});
});
test('Beijing date range includes the entire selected end minute',()=>{
  const query=new URLSearchParams(runSearchParams({...emptyRunFilters,startedFrom:'2026-11-17T08:00',startedTo:'2026-11-17T09:30'}));
  assert.equal(query.get('started_from'),'2026-11-17T00:00:00.000Z');
  assert.equal(query.get('started_to'),'2026-11-17T01:31:00.000Z');
});
test('single-minute and open-ended ranges are supported',()=>{
  const one=new URLSearchParams(runSearchParams({...emptyRunFilters,startedFrom:'2026-11-17T08:00',startedTo:'2026-11-17T08:00'}));
  assert.equal(one.get('started_to'),'2026-11-17T00:01:00.000Z');
  const open=new URLSearchParams(runSearchParams({...emptyRunFilters,startedTo:'2026-11-17T08:00'}));
  assert.equal(open.has('started_from'),false);
});
test('invalid and reversed time ranges are rejected',()=>{
  assert.throws(()=>runSearchParams({...emptyRunFilters,startedFrom:'invalid'}),/有效/);
  assert.throws(()=>runSearchParams({...emptyRunFilters,startedFrom:'2026-11-17T09:00',startedTo:'2026-11-17T08:00'}),/不能早于/);
});
test('reset omits all filters and returns to the first page',()=>{
  assert.deepEqual(Object.fromEntries(new URLSearchParams(runSearchParams(emptyRunFilters))),{limit:'101',offset:'0'});
});
