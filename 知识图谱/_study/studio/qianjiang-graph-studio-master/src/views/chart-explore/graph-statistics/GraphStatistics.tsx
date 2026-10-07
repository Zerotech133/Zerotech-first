import React, { useState,useEffect, useRef} from 'react'
import SvgIcon from '@/components/svg-icon/SvgIcon'
import {Modal, Table, Form, Select, Button ,message} from 'antd'
import {graphStatisticsApi} from "@/api/chart"
import {getSorting, dealNodes, dealEdges } from '@/utils'
import  './GraphStatistics.scss'
import { StrengthCount } from '@/views/chart-explore/form-items/FormItems'
const { Option } = Select


type props = {
  visible:boolean,
  openModal: (flag: boolean) => void,
  edgeTypeNameList: any,
  spaceTagList:any,
  historyData: any,
  mergeData: (data: any) => void,
}
// 图谱统计弹框
const GraphStatistics:React.FC<props> = ({visible, openModal,edgeTypeNameList,spaceTagList, historyData, mergeData}) => {
  const [form] = Form.useForm();
  const [typeValue, settypeValue] = useState<string>('')//切换统计类别时改变对应的类型下拉框的数据
  const [totalData, setTotalData] = useState<any>()
  const [tableData, setTableData] = useState<any>([])
  const [searchVertexList, setSearchVertexList] = useState<any>([])
  const [vertexList, setVertexList] = useState<any>([])
  const [edgeList, setEdgeList] = useState<any>([])
  const [selectedRows, setSelectedRows] = useState<any>([])
  const expandRef = useRef<any>(null)
  const [loading, setLoading] = useState<boolean>(false)

  // 录入历史记录参数
  useEffect(() => {
    if (historyData && historyData?.type === 'statistics') {
      const { mode, edgeTypeName, strengthCondition, countCondition } = JSON.parse(historyData.param)
      form.setFieldsValue({
        mode,
        edgeTypeName,
        strengthCondition,
        countCondition
      })
    }
  }, [historyData, form])
 
  const closeModal = () => {
    openModal(false)
  }
  const selectType =(values:any)=>{
    settypeValue(values)
  }
 

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
      title: '实体类型',
      dataIndex: 'type',
      width: 200
    },
    {
      title: '内容',
      dataIndex: 'content',
    },
    {
      title: '强度',
      dataIndex: 'strength',
    },
    {
      title: '次数',
      dataIndex: 'count',
    }
  ]

  useEffect(() => {
    setTotalData({
        strengthType: '',
        countType: ''
    })
  },[setTotalData])

  // 筛选强度
  const changeStrength = (value: string) => {
    expandRef.current.validateFields([['strengthCondition', 'value']])
    setTotalData({
      ...totalData,
      strengthType: value
    })
  }
  const changeStrengthValue = () => {
    expandRef.current.validateFields([['strengthCondition', 'conditionType']])
  }
  // 筛选次数
  const changeCount = (value: string) => {
    expandRef.current.validateFields([['countCondition', 'value']])
    setTotalData({
      ...totalData,
      countType: value
    })
  }
  const changeCountValue = () => {
    expandRef.current.validateFields([['countCondition', 'conditionType']])
  }
  // 查询
  const onFinish = (values: any) => {
    setLoading(true)
    let {strengthCondition, countCondition} = values
    let sorting = getSorting(strengthCondition, 'strength') || getSorting(countCondition, 'count')
    if (sorting) {
      values.sorting = sorting
    }
    let param={
      ...values
    } 
    graphStatisticsApi(param).then((res: any) => {
      let {edgeList,searchVertexList,vertexList} = res.data
      if (edgeList.length === 0 && searchVertexList.length === 0) {
        setTableData([])
        return
      }
      setEdgeList(edgeList)
      setSearchVertexList(searchVertexList)
      setVertexList(vertexList)
      // 点表格数据
      let nodesList = searchVertexList.map((item: any) => {
        let {id, tagName, name} = item
        return {
          key: id,
          name: 'vertex',
          type: tagName,
          content: name,
          count:item.vertexStatisticsInfo.allCount,
          strength:item.vertexStatisticsInfo.allStrength
        }
      })
      // 边表格数据
      let edgesList = edgeList.map((item: any, index: number) => {
        let { edgeTypeName, sourceId, destinationId, strength ,count} = item
        return {
          key: index,
          name: 'edge',
          type: edgeTypeName,
          content: `${edgeTypeName}: ${sourceId} - ${destinationId} ${strength}`,
          sourceId: sourceId,
          destinationId: destinationId,
          count,
          strength
        }
      })
      if(typeValue==='edge'){
        setTableData([...edgesList])
      }else{
        setTableData([...nodesList])
      }
    }).finally(() => {
      setLoading(false)
    })
  }
  // 重置
  const onReset = () => {
     // 重置表单
     form.resetFields();
  };

  const rowSelection = {
    onChange: (selectedRowKeys: React.Key[],selectedRows: any[]) => {
      setSelectedRows(selectedRows)
    }
  };
  const handleOk = () => {
    let vertexId: any = []
    // 选中边key值集合
    let edgeId: any = []
    if(selectedRows.length){
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
      nodes = dealNodes(nodes)
      let edges = edgeList.filter((item: any, index:number) => edgeId.includes(index))
      edges = dealEdges(edges)
      mergeData({
        highlight: true,
        edges,
        nodes
      })
      openModal(false)
    }else{
      message.warning('请先勾选展示内容')
    }
    
  }

  return(
    <Modal 
    title="图谱统计" 
    visible={visible} 
    className="graph-statistics"
    width={1068}
    okText="图谱展示"
    maskClosable={false}
    onCancel={closeModal}
    onOk={handleOk}
    >
      <Form onFinish={onFinish} form={form} ref={expandRef}>
        <Form.Item
          label="统计类别"
          name="mode"
          style={{minWidth: '240px'}}
          rules={[{required: true, message: '请选择统计类别'}]}
        >
          <Select allowClear={true} placeholder="请选择"  onChange={selectType}>
            <Option value="vertex">实体</Option>
            <Option value="edge">连接</Option>
          </Select>
        </Form.Item>
        <Form.Item 
          label="边类型"
          name="edgeTypeName"
          style={{minWidth: '240px'}}
          rules={[{required: true, message: '请选择类型'}]}
        >
          <Select showSearch allowClear={true} placeholder="请选择">
          {
           edgeTypeNameList?.map((item:any) => <Select.Option key={item.id} value={item.name}>{item.name}</Select.Option>)
          }
          </Select>
        </Form.Item>
        <StrengthCount 
              type={[]}
              strengthType = {totalData?.strengthType}
              countType = {totalData?.countType}
              changeStrength={changeStrength} 
              changeCount={changeCount}
              changeStrengthValue={changeStrengthValue}
              changeCountValue={changeCountValue}
               />
            <br/>
        <Form.Item >
          <Button type="primary" htmlType="submit" style={{margin: '0 20px'}} >查询</Button>
          <Button onClick={onReset}>取消</Button>
        </Form.Item>
      </Form>
      <Table
        rowSelection={{...rowSelection,}}
        columns={columns}
        dataSource={tableData}
        bordered
        pagination={false}
        scroll={{ y: 500 }}
        loading={loading}
      />
    </Modal>
  ) 
}
export default GraphStatistics