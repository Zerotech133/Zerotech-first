import {Modal  } from 'antd'
import React, { useEffect, useState } from 'react'
import { Table, message } from 'antd';
// import  './SearchReturn.scss'
// import { DebounceSelect } from '@/components'
// import { findNodeApi, searchReturnApi, findById } from "@/api/chart"
// import { EdgeTypeItem } from '@/views/chart-explore/form-items/FormItems'
// import { dealNodes, dealEdges } from '@/utils'
// import store from '@/redux/store/index'
// import { setSearchNodes } from '@/redux/action'

// const { Option } = Select

type props = {
  visible:boolean,
  openModal: (flag: boolean) => void,
  hideNodeId: string,
  hideMap: any,
  data: any,
  showNewEdges: (param:any) => void
}

const HideNodesList:React.FC<props> = ({visible, openModal,hideMap, hideNodeId,data,showNewEdges}) => {
  const [tableList, setTableList] = useState<any>([])
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [pagination, setPagination] = useState<any>({
    defaultPageSize: 50,
    pageSizeOptions: ['50', '100'],
    showSizeChanger: true,
    showQuickJumper: true,
    showTotal: (total) => `共 ${total} 条`,
    current: 1,
    pageSize: 50
  })
  const columns: any = [
    {
      title: '序号',
      dataIndex: 'key',
      width: 100,
      render: (txt,record,index) => {
        return (
          <span>
            {(pagination.current - 1)*(pagination.pageSize)+index+1}
          </span>
        )
      }
    },
    {
      title: '对端账号',
      dataIndex: 'opposite'
    },
    {
      title: '对端类型',
      dataIndex: 'oppositeTagName'
    },
    {
      title: '强度',
      dataIndex: 'strength',
      sorter: (a:any, b:any) => a.strength - b.strength,
      defaultSortOrder: 'descend'
    },
    {
      title: '次数',
      dataIndex: 'count',
      sorter: (a:any, b:any) => a.count - b.count
    },
  ];
  const closeModal = () => {
    openModal(false)
  }
  const onSelectChange = (newSelectedRowKeys: React.Key[]) => {
    setSelectedRowKeys(newSelectedRowKeys);
  };

  const rowSelection = {
    selectedRowKeys,
    onChange: onSelectChange,
  };
  useEffect(() => {
    const hideData = hideMap[hideNodeId]
    if (!hideData?.hideNodes) return
    const {hideNodes,hideEdges} = hideData
    const showNodes = data.nodes
    const totalNodes = hideNodes.concat(showNodes)
    const maxNodeId = hideData.maxNodeId
    const tableList1 = []
    hideEdges.forEach((item, index) => {
      const { source, target } = item
      const tableItem = {
        oppositeTagName: '',
        opposite: '',
        key: index + 1,
        ...item
      }
      if (source === maxNodeId) {
        tableItem.opposite = target
        const node = totalNodes.filter(hideNode => hideNode.id === target)
        if (node.length) {
          tableItem.oppositeTagName = node[0].tagName
        }

      } else {
        tableItem.opposite = source
        const node = totalNodes.filter(hideNode => hideNode.id === source)
        if (node.length) {
          tableItem.oppositeTagName = node[0].tagName
        }
      }
      tableList1.push(tableItem)
    })
    setTableList(tableList1)
  }, [hideMap, hideNodeId, data])

  // 分页
  const onChange = (e) => {
    pagination.current = e.current
    pagination.pageSize = e.pageSize
    setPagination(pagination)
  }

  const handleOk = () => {
    if (selectedRowKeys.length === 0) {
      message.warn('请至少选择一条数据')
    } else {
      openModal(false)
      const newShowEdges = tableList.filter(item => selectedRowKeys.includes(item.key))
      showNewEdges(newShowEdges)
    }
  }

  return(
    <Modal
    title="隐藏对端清单"
    visible={visible}
    className="search-return"
    width={1200}
    maskClosable={false}
    okText="上图"
    cancelText="关闭"
    onCancel={closeModal}
    onOk={handleOk}
    >
      <Table
      bordered
      rowSelection={rowSelection}
      columns={columns}
      dataSource={tableList}
      scroll={{ y: 500 }}
      rowKey="key"
      onChange={onChange}
      pagination={pagination}
      sortDirections={['descend', 'ascend']}
       />
    </Modal>
  )
}
export default HideNodesList