import { useEffect, useState } from 'react';
import { Button, Card, Form, Input, Modal, Select, Space, Switch, Table, message } from 'antd';
import { http, errorMessage } from '../../api/client';
export default function Accounts() {
  const [rows,setRows]=useState<any[]>([]),[edit,setEdit]=useState<any>(null),[form]=Form.useForm();
  const refresh=async()=>{try{setRows((await http.get('/api/v1/engine/users')).data.items);}catch(e){message.error(errorMessage(e));}};
  useEffect(()=>{void refresh();},[]);
  const open=(row:any)=>{setEdit(row);form.resetFields();form.setFieldsValue({...row,password:'',is_active:row.is_active??true,role:row.role||'viewer'});};
  const save=async(v:any)=>{try{await http.put('/api/v1/engine/users/'+encodeURIComponent(v.username),v);setEdit(null);await refresh();message.success('账号已保存');}catch(e){message.error(errorMessage(e));}};
  return <Card title="本项目账号" extra={<Button type="primary" onClick={()=>open({})}>添加账号</Button>}><Table rowKey="username" dataSource={rows} columns={[{title:'账号',dataIndex:'username'},{title:'姓名',dataIndex:'display_name'},{title:'角色',dataIndex:'role'},{title:'状态',dataIndex:'is_active',render:v=>v?'启用':'停用'},{title:'操作',render:(_,r)=><Button onClick={()=>open(r)}>编辑</Button>}]}/><Modal open={!!edit} title="账号配置" onCancel={()=>setEdit(null)} footer={null}><Form form={form} layout="vertical" onFinish={save}>
    <Form.Item name="username" label="账号" rules={[{required:true},{pattern:/^[a-zA-Z0-9_@.\-]{1,64}$/,message:'使用字母、数字、下划线、点、@ 或短横线'}]}><Input disabled={!!edit?.username}/></Form.Item>
    <Form.Item name="display_name" label="显示名称"><Input/></Form.Item>
    <Form.Item name="role" label="角色"><Select options={[{value:'admin',label:'管理员'},{value:'operator',label:'操作员'},{value:'viewer',label:'只读用户'}]}/></Form.Item>
    <Form.Item name="password" label="设置密码" extra="至少 12 位；编辑时留空保留原密码。" rules={edit?.username?[]:[{required:true},{min:12}]}><Input.Password autoComplete="new-password"/></Form.Item>
    <Form.Item name="is_active" label="启用" valuePropName="checked"><Switch/></Form.Item><Space><Button type="primary" htmlType="submit">保存账号</Button></Space>
  </Form></Modal></Card>;
}
