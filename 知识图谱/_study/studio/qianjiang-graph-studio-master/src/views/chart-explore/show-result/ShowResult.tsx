import SvgIcon from '@/components/svg-icon/SvgIcon'
import {Modal, Table } from 'antd'
import { useEffect, useState } from 'react'
// import { useState } from 'react'
import  './ShowResult.scss'
import {dealNodes, dealEdges } from '@/utils'


type props = {
  visible:boolean,
  graphData: any,
  openModal: (flag: boolean, str?: string) => void,
  mergeData: (data: any) => void,
  tabType: string
}
// 复杂查询-结果展示
const ShowResult:React.FC<props> = ({visible, graphData, openModal, mergeData, tabType}) => {
  const [selectedRows, setSelectedRows] = useState<any>([])
  const closeModal = () => {
    openModal(false)
  }
  const showGraph = () => {
    openModal(false, 'showGraph')
    let vertexId: any = []
    // 选中边key值集合
    let edgeId: any = []
    selectedRows.forEach((item: any) => {
      if (item.name === 'vertex') { // 点
        vertexId.push(item.key)
      } else { // 边
        vertexId.push(item.sourceId)
        vertexId.push(item.destinationId)
        edgeId.push(item.key)
      }
    })
    // 点去重
    vertexId = [...Array.from(new Set(vertexId))]
    let nodes = vertexList.filter((item: any) => vertexId.includes(item.id))
    nodes = dealNodes(nodes)
    let edges = edgeList.filter((item: any, index:number) => edgeId.includes(index))
    edges = dealEdges(edges)
    mergeData({
      highlight: true,
      edges,
      nodes
    })
  }
  const [tableData, setTableData] = useState<any>([])
  const [vertexList, setVertexList] = useState<any>([])
  const [edgeList, setEdgeList] = useState<any>([])
  useEffect(() => {
     let {edgeList, vertexList} = graphData
      if (vertexList.length === 0 && vertexList.length === 0) {
        setTableData([])
        return
      }
      setEdgeList(edgeList)
      setVertexList(vertexList)
      // 点表格数据
      let nodesList = vertexList.map((item: any) => {
        let {id, tagName, name} = item
        return {
          key: id,
          name: 'vertex',
          type: tagName,
          content: name
        }
      })
      // 边表格数据
      let edgesList = edgeList.map((item: any, index: number) => {
        let { edgeTypeName, sourceId, destinationId, strength } = item
        return {
          key: index,
          name: 'edge',
          type: edgeTypeName,
          content: `${edgeTypeName}: ${sourceId} - ${destinationId} ${strength}`,
          sourceId: sourceId,
          destinationId: destinationId
        }
      })
      if (tabType === '1') {
        setTableData([...nodesList, ...edgesList])
      } else {
        setTableData([...edgesList])
      }
  }, [graphData, tabType])


  const columns = [
    {
      title: '类型',
      dataIndex: 'name',
      render: (text: string) => text === 'vertex'
      ? <SvgIcon iconClass='node-checked'/>
      : <div style={{display: 'flex'}}>
          <SvgIcon iconClass='node-checked'/>
          <div className='line'></div>
          <SvgIcon iconClass='node-checked'/>
        </div>
    },
    {
      title: '内容',
      dataIndex: 'content',
    }
  ]
  interface DataType {
    type: string;
    label: string
  }


  // rowSelection object indicates the need for row selection
  const rowSelection = {
    onChange: (selectedRowKeys: React.Key[], selectedRows: DataType[]) => {
      setSelectedRows(selectedRows)
    }
  };

  return(
    <Modal
    title="结果展示"
    visible={visible}
    className="search-return"
    width={1068}
    maskClosable={false}
    okText="图谱展示"
    onOk={showGraph}
    onCancel={closeModal}
    >
      <Table
        rowSelection={{
          ...rowSelection,
        }}
        columns={columns}
        dataSource={tableData}
        bordered
        pagination={false}
        scroll={{ y: 500 }}
      />
    </Modal>
  )
}
export default ShowResult