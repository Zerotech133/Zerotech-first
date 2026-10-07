import React, { useState, useEffect } from 'react'

import { 
  Select, 
  Collapse, 
  Checkbox, 
  Button, 
  Modal, 
  Table, 
  Space, 
  Input, 
  Form, 
  Popconfirm, 
  message,
  Upload
 } from 'antd'
 import { UploadOutlined } from '@ant-design/icons';
import  './DefineModal.scss'
import { spaceTagCreate, spaceTagUpdate, spaceEdgeCreate, spaceEdgeUpdate } from '@/api/space-edit'

type props = {
  defineName:string,
  defineModalVisible: boolean,
  title: string,
  spaceId: string,
  modalData: any,
  totalTableData: any,
  activeKey: string,
  closeModal: (mode:string) => void
}

const DefineModal: React.FC<props> = ({defineName, defineModalVisible, title, spaceId, modalData, totalTableData, activeKey, closeModal}) => {
  const [tableData, setTableData] = useState<any>(modalData.propertyInfoList)
  let [scrollHeight, setScrollHeight] = useState(300)
  const [isChecked, setIsChecked] = useState(true)
  const [defaultActiveKey, setDefaultActiveKey] = useState<any>(['1'])
  const [form] = Form.useForm()
  const [editingKey, setEditingKey] = useState('');
  const [isDisabled, setIsDisabled] = useState(false);
  const [strengOptions, setStrengOptions] = useState<any>([])
  const isEditing = (record: Item) => record.key === editingKey;
  const [iconPath, setIconPath] = useState('')

  interface Item {
    key: string;
    propertyType: string;
    nullable: boolean;
  }

  interface EditableCellProps extends React.HTMLAttributes<HTMLElement> {
    editing: boolean;
    dataIndex: string;
    title: any;
    record: Item;
    index: number;
    children: React.ReactNode;
  }

  const options = [
    {value:'string',label:'string'},
    {value:'number',label:'number'},
    {value:'date',label:'date'},
    {value:'integer',label:'integer'}
  ]

  const EditableCell: React.FC<EditableCellProps> = ({
    editing,
    dataIndex,
    record,
    title,
    children,
    ...restProps
  }) => {
    const domTypes:any = {
      key: <Input />,
      propertyType: <Select filterOption={false} options = {options}/>,
      nullable: <Checkbox defaultChecked={record?.nullable}></Checkbox>
    }
    return (
      <td {...restProps}>
        {editing ? (
          <Form.Item
            name={dataIndex}
            style={{ margin: 0 }}
            valuePropName={dataIndex==='nullable'?'checked':undefined}
            rules={[
              { required: dataIndex!=='nullable', message: `请输入${title}!` },
              {validator(_, value) {
                if (dataIndex === 'key') {
                  if (value) {
                    const findIndex = tableData.findIndex((item:any) => {return item.key === value})
                    const isEmpty = tableData.some((item:any) => {return item.key === ''})
                    if (findIndex > -1 && isEmpty) {
                      return Promise.reject(new Error('属性名称重复!'))
                    } else {
                      return Promise.resolve()
                    }
                  } else {
                    return Promise.resolve()
                  }
                }
                return Promise.resolve()
              }}
            ]}
          >
            {
              domTypes[dataIndex]
            }
          </Form.Item>
        ) : (
          children
        )}
      </td>
    );
  }

  const edit = (record: Partial<Item> & { key: React.Key }) => {
    setIsDisabled(true)
    form.setFieldsValue(record);
    setEditingKey(record.key);
  };
  // 取消属性
  const cancel = () => {
    if (editingKey === '') {
      // 新增属性取消
      setTableData(tableData.slice(0,tableData.length - 1))
    } else {
      setEditingKey('');
    }
    form.setFieldsValue({key: '', propertyType: '', nullable: true})
    // 添加属性按钮恢复
    setIsDisabled(false)
  };
  // 保存属性
  const save = async (key: React.Key) => {
    try {
      const row = (await form.validateFields()) as Item;
      const newData = [...tableData];
      const index = newData.findIndex(item => key === item.key);
      if (index > -1) {
        const item = newData[index];
        newData.splice(index, 1, {
          ...item,
          ...row,
        });
        setTableData(newData);
        setEditingKey('');
      } else {
        newData.push(row);
        setTableData(newData);
        setEditingKey('');
      }
      setStrengOptions(newData.map((item:any) => {return {value: item.key, label: item.key}}))
      // 上一次保存的form数据清空，避免添加属性的时候有默认值
      form.setFieldsValue({key: '', propertyType: '', nullable: true})
      setIsDisabled(false)
    } catch (errInfo) {
      console.log('Validate Failed:', errInfo);
    }
  };
  // 删除属性
  const handleDelete = (key: React.Key) => {
    let data = tableData.filter((item:any) => {return item.key !== key})
    setTableData(data)
    if (activeKey === 'edge') {
      // 删除的属性，强度下拉不显示
      setStrengOptions(data.map((item:any) => {return {value: item.key, label: item.key}}))
    }
  };
  // 点击添加属性按钮
  const handleAdd = () => {
    const newData: any = {
      propertyType: '',
      key: '',
      nullable: true
    };
    setIsDisabled(true)
    setTableData([...tableData, newData]);
  };
 

  const columns = [
    {
      title: '属性名称',
      key: 'key',
      dataIndex: 'key',
      editable: true
    },
    {
      title: '数据类型',
      key: 'propertyType',
      dataIndex: 'propertyType',
      editable: true
    },
    {
      title: '允许空值',
      editable: true,
      key: 'nullable',
      dataIndex: 'nullable',
      render: (text:any, record:any) => (
        <Checkbox checked={record.nullable} disabled={!isEditing(record)}></Checkbox>
      )
    },
    {
      title: '操作',
      key: 'action',
      render: (_: any, record: Item) => {
        const editable = isEditing(record);
        return editable ? (
          <span>
            <Button type="link" onClick={() => save(record.key)}>保存</Button>
            <Popconfirm title="确认取消此数据项吗？" onConfirm={cancel}>
              <Button type="link">取消</Button>
            </Popconfirm>
          </span>
        ) : (
          <span>
            <Button type="link" disabled={isDisabled} onClick={() => {edit(record)}}>编辑</Button>
            <Popconfirm title="确认删除此数据项吗？" onConfirm={() => handleDelete(record.key)}>
              <Button type="link">删除</Button>
            </Popconfirm>
          </span>
        );
      },
    },
  ];
  const mergedColumns = columns.map(col => {
    if (!col?.editable) {
      return col;
    }
    return {
      ...col,
      onCell: (record: Item) => ({
        record,
        title: col.title,
        dataIndex: col.dataIndex,
        editing: isEditing(record),
      }),
    };
  })

  const handleHeight = () => {
    let height = window.innerHeight - 100 - 450
    setScrollHeight(height)
  }
  useEffect(() => {
    handleHeight()
    // 查询图空间列表
    window.addEventListener('resize',handleHeight)
    return () => {
      window.removeEventListener('resize',handleHeight)
    }
   },[])

  useEffect(() => {
    setStrengOptions(tableData.map((item:any) => {return {value: item.key, label: item.key}}))
   },[tableData])
   
  
  const handleCancel = () => {
    closeModal('cancel')
  }
 
  const onChange = (e:any) => {
    setIsChecked(e.target.checked)
    e.target.checked === false ? setDefaultActiveKey([]) : setDefaultActiveKey(['1'])
  }
  const onFinish = (value: any) => {
    if (title.includes('新增')) {
      let api
      let param
      if (activeKey === 'label') {
        api = spaceTagCreate
        let iconPath = value?.upload?.length 
          ? value.upload[0].response.data.filePath
          : ''
        param = {spaceId, name: value.name, propertyInfoList:tableData, iconPath}
      } else {
        api = spaceEdgeCreate
        param = {spaceId,...value,propertyInfoList:tableData}
      }
      api(param).then((res:any) => {
        closeModal('save')
        message.success('新增成功');
      })
    } else {
      let someExist = tableData.some((item:any) => {return item.key === ''})
      if (someExist) {
        message.warning('请先保存属性');
        return
      }
      const api = activeKey === 'label' ? spaceTagUpdate : spaceEdgeUpdate
      let param:any = {
        id: modalData.key, 
        propertyInfoList: tableData
      }
      if (activeKey === 'edge') {
        param = {...param, strengthPropertyName: value.strengthPropertyName || ''}
      } else {
        param = {...param, iconPath: iconPath}
      }
      api(param).then((res:any) => {
        closeModal('save')
        message.success('编辑成功');
      })
    }
  }
  const normFile = (e: any) => {
    if (Array.isArray(e)) {
      return e;
    }
    return e?.fileList;
  };
  const uploadPicture = (value: any) =>{
    if (value.file.status !== 'uploading') {
      let fileList = value.fileList
      if(fileList.length) {
        let icon = fileList[0].response.data.filePath
        setIconPath(icon)
      } else {
        setIconPath('')
      }
    }
  }

  return (
    <>
      <Modal 
      visible={defineModalVisible} 
      footer={null}
      destroyOnClose
      width={'100%'}
      title={title}
      className="defineModal"
      maskClosable={false}
      onCancel={handleCancel} 
      >
        <Form 
        onFinish={onFinish}  
        wrapperCol={{ span: 4 }} 
        autoComplete="off"
        initialValues={{strengthPropertyName:modalData.strengthPropertyName}}
        >
          <Space direction="vertical" style={{ width: '100%' }}>
            {title.includes('新增') ? 
            <Form.Item
              label="名称"
              name="name"
              rules={[
                { required: true, message: '请输入名称!'},
                {validator: (_,value) => {
                  if (value) {
                    const findIndex = totalTableData.findIndex((item:any) => {return item.name === value})
                    if (findIndex > -1) {
                      return Promise.reject(new Error('名称重复!'))
                    } else {
                      return Promise.resolve()
                    }
                  } else {
                    return Promise.resolve()
                  }
                }}
              ]}
            >
              <Input placeholder="请输入名称" />  
            </Form.Item>
            : <div>名称: {modalData.name}</div>}
            {activeKey === 'label'&&
            <Form.Item
              name="upload"
              label="上传图标"
              valuePropName="fileList"
              getValueFromEvent={normFile}
            >
              <Upload 
              name="file" 
              action="/api/file/upload" 
              listType="picture"
              maxCount={1}
              defaultFileList={modalData.iconPath? [{
                uid: '2',
                name: 'image.png',
                status: 'done',
                url: '/api/file' + modalData.iconPath,
              }]:[]}
              onChange={uploadPicture}
              >
                <Button icon={<UploadOutlined />}>Click to upload</Button>
              </Upload>
            </Form.Item>}
            <Collapse bordered={false} activeKey={defaultActiveKey}>
              <Collapse.Panel 
              header={<Checkbox onChange={onChange} checked={isChecked}>定义属性</Checkbox>}  
              key="1">
                <Form form={form} component={false}>
                  <Table
                    components={{
                      body: {
                        cell: EditableCell,
                      },
                    }}
                    bordered
                    dataSource={tableData}
                    columns={mergedColumns}
                    rowClassName="editable-row"
                    scroll={{ y: scrollHeight }}
                    pagination={false}
                  />
                </Form>
                <Button type='primary' style={{marginTop: '20px'}} onClick={handleAdd} disabled={isDisabled?true:false}>添加属性</Button>
              </Collapse.Panel>
            </Collapse>
            {activeKey === 'edge' && <Form.Item 
              label="强度" 
              name="strengthPropertyName" 
             >
              <Select
                showSearch
                allowClear
                placeholder="请选择强度"
                optionFilterProp="children"
                options={strengOptions}
                filterOption={(input, option:any) => option?.value?.toLowerCase().indexOf(input.toLowerCase()) > -1
                }
              />
            </Form.Item>}
            <Form.Item wrapperCol={{ offset: 8, span: 16 }}>
              <Button type="primary" htmlType="submit">
                保存
              </Button>
            </Form.Item>
          </Space>
        </Form>
      </Modal>
      
      
    </>

  )
}

export default DefineModal
