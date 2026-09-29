/** PR76 嵌套参数的草稿、保存及历史预览回归；不调用真实模型或浏览器。 */
import {expect, test} from 'bun:test';
import {act, fireEvent, render, renderHook, screen, waitFor} from '@testing-library/react';
import {useState} from 'react';
import {ParametersPanel} from '@/features/remotion_templates/ParametersPanel';
import {sameValues, type Values, type Version} from '@/features/remotion_templates/model';
import {useTemplateSession} from '@/features/remotion_templates/useTemplateSession';
import {remotionVersion, remotionJob} from './remotion-fixtures';
import {remotionServer} from './remotion-server';
import {fetchMock} from './setup';

/** 创建包含独立实例参数的新版公开结果。 */
function version(): Version {
  const result = remotionVersion();
  result.spec.schema_version = '2';
  result.spec.sprite_kind = 'composition';
  result.spec.text_layers = [];
  result.candidate.default_config = {title:{text:'今日灵感',color:'#fff'},description:{text:'说明'}};
  result.candidate.config_schema.properties = {title:{type:'object'},description:{type:'object'}};
  return result;
}

// 新版参数按实例编辑，非法 JSON 阻止保存；修复后更新本地草稿。
test('嵌套参数支持实时草稿且非法 JSON 不能保存', () => {
  const saved: Values[] = [];
  function Panel() {
    const source = version();
    const [values, setValues] = useState(source.candidate.default_config);
    return <ParametersPanel version={source} values={values} disabled={false} pending={false} dirty={!sameValues(values,source.candidate.default_config)} saving={false}
      onChange={(key,value) => setValues(previous=>({...previous,[key]:value}))} onSave={()=>{saved.push(values);}} onDiscard={()=>setValues(source.candidate.default_config)} />;
  }
  render(<Panel/>);
  const field = screen.getByLabelText('实例参数 · title');
  fireEvent.change(field,{target:{value:'{"text":"新标题","color":"#fff"}'}});
  const save = screen.getByRole('button',{name:'保存配置'});
  expect(save.hasAttribute('disabled')).toBe(false);
  fireEvent.change(field,{target:{value:'{'}});
  expect(save.hasAttribute('disabled')).toBe(true);
  expect(screen.getByRole('alert')).toBeTruthy();
  fireEvent.change(field,{target:{value:'{"text":"新标题","color":"#fff"}'}});
  fireEvent.click(save);
  expect(saved[0]).toEqual({title:{text:'新标题',color:'#fff'},description:{text:'说明'}});
});

// 会话保存只提交变化的实例，其余参数和服务端成功基线保持不变。
test('会话保存嵌套参数的净变化',async()=>{
  remotionServer(path=>path==='/versions/version-1'?Response.json(version()):path.endsWith('/messages')?Response.json(remotionJob('running')):undefined);
  const {result}=renderHook(()=>useTemplateSession(()=>{}));
  act(()=>result.current.send('制作组合'));
  await waitFor(()=>expect(result.current.version?.spec.schema_version).toBe('2'));
  act(()=>result.current.change('title',{text:'新标题',color:'#fff'}));
  expect(result.current.dirty).toBe(true);
  act(()=>result.current.saveParameters());
  await waitFor(()=>expect(fetchMock.mock.calls.some(([url])=>String(url).endsWith('/messages'))).toBe(true));
  const request=fetchMock.mock.calls.find(([url])=>String(url).endsWith('/messages'))!;
  expect(JSON.parse(String(request[1]?.body)).parameters).toEqual({title:{text:'新标题',color:'#fff'}});
});
