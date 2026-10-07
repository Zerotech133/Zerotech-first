import React, {useState,useEffect} from 'react'
import SvgIcon from '@/components/svg-icon/SvgIcon'
import {Modal, Table} from 'antd'
import { dealNodes, dealEdges} from '@/utils'
import  './FindText.scss'

type props = {
  findTextData: any,
  visible:boolean,
  loading:boolean,
  mergeData:(data: any) => void,
  openModal: (flag: boolean) => void,
  typeValue:string
}
// 查找文本弹框
const FindText:React.FC<props> = ({mergeData,findTextData,visible, loading, openModal,typeValue}) => {
  const [selectedRows, setSelectedRows] = useState<any>([])
  const [tableData, setTableData] = useState<any>([])
  const [searchVertexList, setSearchVertexList] = useState<any>([])
  const [vertexList, setVertexList] = useState<any>([])
  const [edgeList, setEdgeList] = useState<any>([])
  const closeModal = () => {
    openModal(false)
  } 
  useEffect(() => {
      if(findTextData){
        let {edgeList,searchVertexList,vertexList} = findTextData
        setEdgeList(edgeList)
        setSearchVertexList(searchVertexList)
        setVertexList(vertexList)
        // 点表格数据
        let nodesList = searchVertexList?.map((item: any) => {
          let {id, tagName, name} = item
          return {
            key: id,
            name: 'vertex',
            type: tagName,
            content: name
          }
        })
        // 边表格数据
        let edgesList = edgeList?.map((item: any, index: number) => {
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
        if(typeValue==='edge'){
          setTableData([...edgesList])
        }else if(typeValue==='vertex'){
          setTableData([...nodesList])
        }else{
          setTableData([...nodesList, ...edgesList])
        }
      }
  },[findTextData,typeValue])

  const columns = [
    {
      title: '统计类型',
      dataIndex: 'name',
      width: 200,
      render: (text: string) => text === 'vertex' 
      ? <SvgIcon iconClass='node-checked'/> 
      : <div style={{display: 'flex'}}>
          <SvgIcon iconClass='node-checked'/> 
          <div className='line'></div>
          <SvgIcon iconClass='node-checked'/> 
        </div>
    },
    {
      title: '类型',
      dataIndex: 'type',
      width: 200
    },
    {
      title: '内容',
      dataIndex: 'content',
    }
  ]
  const handleOk = () => {
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
    let endVertexList=typeValue=== 'vertex'?searchVertexList:vertexList
    let nodes = endVertexList.filter((item: any) => vertexId.includes(item.id))
    let edges = edgeList.filter((item: any, index:number) => edgeId.includes(index))
    nodes = dealNodes(nodes)
    edges = dealEdges(edges)
    mergeData({
      highlight: true,
      edges,
      nodes
    })
    openModal(false)
  }
  const rowSelection = {
    onChange: (selectedRowKeys: React.Key[], selectedRows: any[]) => {
      setSelectedRows(selectedRows)
     }
  };
  return(
    <Modal 
    title="查找文本" 
    visible={visible} 
    className="find-text"
    width={1068}
    onCancel={closeModal}
    onOk={handleOk}
    okText="图谱展示"
    style={{overflow:'hidden'}}
    maskClosable={false}
    >
      <Table
        rowSelection={{...rowSelection}}
        columns={columns}
        dataSource={tableData}
        bordered
        pagination={{ 
          defaultPageSize: 50,
          pageSizeOptions: ['50', '100'],
          showSizeChanger: true,
          // showQuickJumper: true,
          showTotal: (total) => `共 ${total} 条`
        }}
        scroll={{ y: 500 }}
        loading={loading}
      />
    </Modal>
  ) 
}
export default FindText