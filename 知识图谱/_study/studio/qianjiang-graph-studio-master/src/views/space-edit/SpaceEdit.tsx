import React, { useState, useEffect, useCallback } from 'react'

import { Card, Input, Button, Table, Tabs, Space, message, Popconfirm } from 'antd'
import  './SpaceEdit.scss'
import { useNavigate, useLocation } from 'react-router-dom'
import {getSpaceTagList, deleteSpaceTag, getSpaceEdgeList, deleteSpaceEdge} from '@/api/space-edit'
import DefineModal from './components/define-modal/DefineModal'

const { TabPane } = Tabs
const SpaceEdit: React.FC = () => {
  const [tableData, setTableData] = useState<any>([])
  const [defineName, setDefineName] = useState('')
  const [defineModalVisible, setDefineModalVisible] = useState(false)
  const [title, setTitle] = useState('新增')
  const navigate = useNavigate()
  const spaceId:any = useLocation().state
  const [modalData, setModalData] = useState<any>({ key: '', propertyInfoList: [] })
  const [totalTableData, setTotalTableData] = useState<any>([])
  const [activeKey, setActiveKey] = useState('label')

  const columns = [
    {
      title: '序号',
      render: (text:any,record:any,index:any) => index + 1
    },
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name'
    },
    {
      title: '操作',
      key: 'action',
      render: (text:any, record:any) => (
          <Space size="middle">
            <Button type="link" onClick={() => {editTag(record)}}>编辑</Button>
            <Popconfirm title="确认删除此数据项吗？" onConfirm={() => deleteTag(record.key)}>
              <Button type="link">删除</Button>
            </Popconfirm>
          </Space>
      )
    },
  ];
  // 删除标签
  const deleteTag = (id:string) => {
    const Api = activeKey === 'label' ? deleteSpaceTag : deleteSpaceEdge
    Api({id}).then(() => {
      message.success('删除成功')
      selectSpaceTagList(activeKey)
    })
  }
  // 编辑标签属性
  const editTag = (record:any) => {
    let title = ''
    if (record.name) {
      title = '编辑标签属性'
      setModalData(record)
    } else {
      title = '新增标签属性'
      setModalData({ key: '', propertyInfoList: [] })
    }
    setTitle(title)
    setDefineName(record.name)
    setDefineModalVisible(true)
  }
  // 查询空间标签
  const selectSpaceTagList = useCallback((activeKey:string) => {
    let Api = activeKey === 'label' ? getSpaceTagList : getSpaceEdgeList
    Api({spaceId}).then((res:any) => {
      let tableData = res?.data
      if (tableData) {
        tableData.forEach((item:any) => {item.key = item.id})
        setTableData(tableData)
        setTotalTableData(tableData)
      }
    })
  }, [spaceId])
  
  useEffect(() => {
    // 查询图空间列表
    selectSpaceTagList(activeKey)
  },[selectSpaceTagList, activeKey])
   
  
  // 返回 
  function changePath() {
    navigate('/schema')
  }
  // 搜索框搜索
  const onSearch = (value:any) => {
    let tableData = totalTableData.filter((item:any) => {return item.name.includes(value)})
    setTableData(tableData)
  }
  const closeModal = (mode:string) => {
    setDefineModalVisible(false)
    mode === 'save' && selectSpaceTagList(activeKey)
  }
  // tab切换
  const onChange = (e: any) => {
    setActiveKey(e)
    selectSpaceTagList(e)
  }

  
  return (
    <>
      <Card title="当前图空间" extra={<Button onClick={changePath}>返回图空间列表</Button>} className="spaceEdit">
        <Tabs tabPosition="left" onChange={onChange}>
          <TabPane tab="标签" key="label">
            <Space direction="vertical" size="large" style={{width: '100%'}}>
              <Button type="primary" onClick={editTag}><span className='create-text'>创建</span></Button>
              <Input.Search placeholder="请输入搜索名称" allowClear onSearch={onSearch} style={{ width: 200 }} />
              <Table 
              columns={columns} 
              dataSource={tableData} 
              pagination={false}
              />
            </Space>
          </TabPane>
          <TabPane tab="边类型" key="edge">
            <Space direction="vertical" size="large" style={{width: '100%'}}>
              <Button type="primary" onClick={editTag}><span className='create-text'>创建</span></Button>
              <Input.Search placeholder="请输入搜索名称" allowClear onSearch={onSearch} style={{ width: 200 }} />
              <Table 
              columns={columns} 
              dataSource={tableData} 
              pagination={false}
              />
            </Space>
          </TabPane>
        </Tabs>
        
      </Card>
      {/* 定义属性弹框 */}
      {defineModalVisible&&
        <DefineModal 
        defineName={defineName} 
        defineModalVisible={defineModalVisible} 
        title={title} 
        spaceId={spaceId}
        modalData={modalData}
        totalTableData={totalTableData}
        activeKey={activeKey}
        closeModal={closeModal} 
      />}
    </>

  )
}

export default SpaceEdit
