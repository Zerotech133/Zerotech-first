import React, { useEffect, useCallback, useState } from 'react'

import { Card, Button, Modal, Table, Form, Space, Input, message, Popconfirm, InputNumber } from 'antd'
import  './Schema.scss'
import {getSpaceList, spaceCreate, deleteSpace, importSpaceDataApi} from '@/api/schema'
import { useNavigate } from 'react-router-dom'

const Schema: React.FC = () => {

  const [createModalVisible, setCreateModalVisible] = useState<boolean>(false)
  const [tableData, setTableData] = useState<any>([])
  const [scrollHeight, setScrollHeight] = useState(300)
  const [modalVisible, setModalVisible] = useState(false)
  const [spaceId, setSpaceId] = useState<string>('')
  const [pagination, setPagination] = useState<any>({
    current: 1,
    pageSize: 10,
    total: 0,
    pageSizeOptions: ['10', '30', '60'],
    showQuickJumper: true,
    showSizeChanger: true,
    showTotal: (count = pagination.total) => {
      return '共' + count + '条数据'
    }
  })
  const navigate = useNavigate()
  // 删除空间列表
  const deleteList = (key:string) => {
    deleteSpace({id: key}).then((res:any) => {
      message.success('删除成功');
      selectSpaceList()
    })
  }
  // 编辑图空间
  const toSpaceEdit = (key: string) =>{
    navigate('/space-edit', { state: key })
  }
  // 图空间导入数据
  const toImport = (key: string) => {
    setModalVisible(true)
    setSpaceId(key)
  }
  const columns = [
    {
      title: '序号',
      render: (text:any,record:any,index:any) => index + 1
    },
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      render: (text:any, record:any) => (
        <Button type="link" onClick={() => {toSpaceEdit(record.key)}}>{record.name}</Button>
    )
    },
    {
      title: '操作',
      key: 'action',
      render: (text:any, record:any) => (
          <Space size="middle">
            <Button type="link" onClick={() => {toImport(record.key)}}>导入随机数据</Button>
            {/* <Button type="link" onClick={() => {toSpaceEdit(record.key)}}>编辑</Button> */}
            <Popconfirm title="确认删除此数据项吗？" onConfirm={() => deleteList(record.key)}>
              <Button type="link">删除</Button>
            </Popconfirm>
          </Space>
      )
    },
  ];
  // 查询图空间列表
  const selectSpaceList = useCallback((param:any={pageNum: 1, pageSize: 10}) => {
    getSpaceList(param).then((res:any) => {
      if (res?.data?.list?.length) {
        let tableData = res.data.list
        tableData = tableData.map((item:any) => {return {key:item.id,name:item.name}} )
        setTableData(tableData)
      }
      let paginations:any = {
        current: param.pageNum,
        pageSize: param.pageSize,
        total: res?.data?.total,
        pageSizeOptions: ['10', '30', '60'],
        showQuickJumper: true,
        showSizeChanger: true,
        showTotal: (count = res?.data?.total) => {
          return '共' + count + '条数据'
        }
      }
      setPagination(paginations)
    })
  }, [])
 
   useEffect(() => {
    let dom = document.getElementsByClassName('ant-space')[0]
    let height = dom.scrollHeight - 160
    setScrollHeight(height)
    // 查询图空间列表
    selectSpaceList()
   },[selectSpaceList])

  // 打开创建空间的弹框
  const showCreateModal = () =>{
    setCreateModalVisible(true)
  }
  
  const handleCancel = () => {
    setCreateModalVisible(false)
  }
  // 创建图空间列表
  const onFinish = (param:any) => {
    spaceCreate(param).then(() => {
      setCreateModalVisible(false)
      message.success('创建成功');
      selectSpaceList()
    })
  }
  // 分页size变化
  const handelTableChange = (e:any) =>{
    let {current, pageSize} = e
    let param = {pageNum: current, pageSize}
    selectSpaceList(param)
  }
  const onParamFinish = (value: any) => {
    importSpaceDataApi({spaceId, ...value}).then((res: any) => {
      message.success('导入成功');
      setModalVisible(false)
    })
  }
  const handleParamCancel = () => {
    setModalVisible(false)
  }

  
  return (
  <>
    <Card title="图空间列表" bordered={false} className="schema">
      <Space direction="vertical" size="large" style={{width: '100%'}}>
          <Button type="primary" onClick={showCreateModal}><span className='create-text'>创建</span></Button>
          {/* 创建图空间弹框 */}
          <Modal maskClosable={false} visible={createModalVisible} onCancel={handleCancel} footer={null} destroyOnClose>
            <Form
              labelCol={{ span: 4 }}
              wrapperCol={{ span: 8 }}
              onFinish={onFinish}
            >
              <Form.Item
                label="名称"
                name="name"
                rules={[{ required: true, message: '请输入名称' }]}
                style={{height:'60px'}}
              >
                <Input />
              </Form.Item>
              <Form.Item wrapperCol={{ offset: 10, span: 12 }} style={{marginTop: '24px',marginBottom: 0}}>
                <Button type="primary" htmlType="submit">
                  创建
                </Button>
              </Form.Item>
            </Form>
          </Modal>
        <Table 
        columns={columns} 
        dataSource={tableData} 
        scroll={{ y: scrollHeight }}
        pagination={pagination}
        onChange={handelTableChange}
        />
      </Space>
    </Card>
    {/* 导入模态框弹框 */}
    <Modal maskClosable={false} title="随机参数" visible={modalVisible} onCancel={handleParamCancel} footer={null} destroyOnClose>
      <Form
        labelCol={{ span: 4 }}
        wrapperCol={{ span: 8 }}
        onFinish={onParamFinish}
      >
        <Form.Item
          label="点的数量"
          name="sampleSize"
          rules={[{ required: true, message: '请输入' }]}
          initialValue={100}
        >
          <InputNumber style={{width: '100%'}} min={2} />
        </Form.Item>
        <Form.Item
          label="边的数量"
          name="edgeNum"
          rules={[{ required: true, message: '请输入' }]}
          initialValue={5}
        >
          <InputNumber style={{width: '100%'}} min={1} max={50} />
        </Form.Item>
        <Form.Item wrapperCol={{ offset: 10, span: 12 }} style={{marginTop: '24px',marginBottom: 0}}>
          <Button type="primary" htmlType="submit">
            确定
          </Button>
        </Form.Item>
      </Form>
    </Modal>
  </>
  )
}

export default Schema
