type PreviousInputs = {run_id: string | null; inputs: Record<string, unknown>; omitted_secrets: string[]};

export function testRunInputs(previous: PreviousInputs, parametersText: string) {
  const parameters = JSON.parse(parametersText);
  if (!Array.isArray(parameters)) throw new Error('参数定义须为 JSON 数组，请修正后测试运行');
  const inputs = {...previous.inputs};
  const omitted = new Set(previous.omitted_secrets);
  for (const parameter of parameters) {
    if (parameter?.secret && Object.hasOwn(inputs, parameter.name)) {
      delete inputs[parameter.name];
      omitted.add(parameter.name);
    }
  }
  const hint = previous.run_id ? '已加载你上次运行此资源时的参数，可修改后执行。' : '暂无历史运行参数，请填写本次参数。';
  return {
    json: JSON.stringify(inputs, null, 2),
    hint: hint + (omitted.size ? ' 以下秘密参数需重新填写：' + [...omitted].join('、') + '。' : ''),
  };
}
