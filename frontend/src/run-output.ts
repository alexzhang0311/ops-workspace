type Data = Record<string, any>;

export function runOutput(run: Data): unknown {
  const result = run.result || {};
  if (run.kind === 'app') {
    return {
      outputs: result.outputs ?? result.value ?? {},
      final_result: result.final_result ?? null,
    };
  }
  return Object.hasOwn(result, 'value') ? result.value : Object.keys(result).length ? result : undefined;
}
