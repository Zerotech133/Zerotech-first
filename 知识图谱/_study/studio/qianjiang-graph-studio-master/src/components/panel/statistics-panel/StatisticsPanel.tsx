import React, { useCallback,useEffect, useState, useRef, useContext} from 'react'
import {Button, Form, Table, Space, Select} from 'antd'
import {GraphinContext} from "@antv/graphin"
import { StrengthCount } from '@/views/chart-explore/form-items/FormItems'
import './StatisticsPanel.scss'
import store from "@/redux/store"
import { setGraphData } from '@/redux/action/index'
import mouseMethod from '@/utils/mouseMethod'
import { setSearchNodes } from '@/redux/action'

type props = {
  edgeTypeNameList: any,
  visible:boolean,
  data: any,
  hideMap: any,
  mergeData: (data: any) => void,
}

const StatisticsPanel: React.FC<props> = ({visible, edgeTypeNameList, data, hideMap, mergeData }) => {
  const [tableDataList, setTableDataList] = useState<any>([])
  const [totalData, setTotalData] = useState<any>({})
  const [scrolly, setScrolly] = useState(500)
  const expandRef = useRef<any>(null)
  const {graph} = useContext(GraphinContext)
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([])

  const onSelectChange = (keys) => {
    setSelectedRowKeys(keys)
    if (keys.length) {
      // 隐藏点上图
      const graphData = mouseMethod.getNewNodesEdges(data, keys)
      store.dispatch(setSearchNodes({searchNodes: []}))
      mergeData({...graphData, highlight: true})
    }
    if (keys.length === 0) {
      graph.getEdges().forEach(it => {
        graph.clearItemStates(it)
      })
      graph.getNodes().forEach(it => {
        graph.clearItemStates(it)
      })
    }
    const edgesData = graph.findAllByState("edge", 'actived')
    const nodesData = graph.findAllByState("node", 'obvious')
    store.dispatch(setGraphData({ edgesData, nodesData }))
  }

  const rowSelection = {
    selectedRowKeys,
    onChange: onSelectChange,
  }

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
  const columns = [
    {
      title: '主端',
      dataIndex: 'source',
    },
    {
      title: '对端',
      dataIndex: 'target'
    },
    {
      title: '强度',
      dataIndex: 'strength',
      sorter: (a:any, b:any) => a.strength - b.strength
    },
    {
      title: '次数',
      dataIndex: 'count',
      sorter: (a:any, b:any) => a.count - b.count
    },
  ];
  useEffect(() => {
    setTotalData({
        strengthType: '',
        countType: ''
    })
  },[setTotalData])

  useEffect(() => {
    setTableDataList(data.edges)
  }, [data])

  useEffect(() => {
    let dom = document.querySelector('.statisticsPanel') as HTMLElement
    let scrolly = dom.offsetHeight - 40 - 160 - 50 - 90
    setScrolly(scrolly)
  }, [])

  function onFinish(values: any) {
    const hideEdges:any = []
    // 隐藏边
    for(let prop in hideMap) {
      hideEdges.push(hideMap[prop].hideEdges)
    }
    let tableData: any = data.edges.concat(hideEdges)
    // 选择边类型
    if (values.edgeTypeName) {
      tableData = data.edges.filter((item: any) => item.edgeTypeName === values.edgeTypeName)
    }
    // 选中强度
    let strengthSort
    if (values?.strengthCondition?.conditionType) {
      let conditionType = values?.strengthCondition?.conditionType
      let value = values?.strengthCondition?.value
      switch(conditionType) {
        case '大于':
          tableData = tableData.filter((item: any) => item.strength > value)
          break
        case '小于':
          tableData = tableData.filter((item: any) => item.strength < value)
          break
        case '等于':
          tableData = tableData.filter((item: any) => item.strength === value)
          break
        default:
          strengthSort = conditionType
      }
    }
    // 选中次数
    let countSort
    if (values?.countCondition?.conditionType) {
      let conditionType = values?.countCondition?.conditionType
      let value = values?.countCondition?.value
      switch(values?.countCondition?.conditionType) {
        case '大于':
          tableData = tableData.filter((item: any) => item.count > value)
          break
        case '小于':
          tableData = tableData.filter((item: any) => item.count < value)
          break
        case '等于':
          tableData = tableData.filter((item: any) => item.count === value)
          break
        default:
          countSort = conditionType
      }
    }
    if (strengthSort === '最大') {
      tableData = tableData.sort((a: any, b: any) => b.strength - a.strength).slice(0, values?.strengthCondition?.value)
    } else if (strengthSort === '最小') {
      tableData = tableData.sort((a: any, b: any) => a.strength - b.strength).slice(0, values?.strengthCondition?.value)
    }
    if (countSort === '最大') {
      tableData = tableData.sort((a: any, b: any) => b.count - a.count).slice(0, values?.countCondition?.value)
    } else if (countSort === '最小') {
      tableData = tableData.sort((a: any, b: any) => a.count - b.count).slice(0, values?.countCondition?.value)
    }
    setTableDataList(tableData)
  }


  const handleClick = useCallback((record) => {
    graph.getEdges().forEach(it => {
      graph.clearItemStates(it)
      graph.setItemState(it, 'disabled', false)
      graph.setItemState(it, 'actived', false)
      const model = it.getModel()
      if (model.source===record.source&&model.target===record.target) {
        graph.setItemState(it, 'actived', true)
      }else{
        graph.setItemState(it, 'disabled', true)
      }
    })
    graph.getNodes().forEach(it => {
      graph.clearItemStates(it)
      graph.setItemState(it, 'obvious', false)
      graph.setItemState(it, 'disabled', false)
      const model = it.getModel()
      if (model.id===record.source||model.id===record.target) {
          graph.setItemState(it, 'obvious', true)
      } else {
        graph.setItemState(it, 'disabled', true)
      }
    })
    const edgesData = graph.findAllByState("edge", 'obvious')
    const nodesData = graph.findAllByState("node", 'obvious')
    store.dispatch(setGraphData({ edgesData, nodesData }))
  }, [graph])
  return (
    <>
      <div className="statisticsPanel" style={{display:visible?"block":"none"}}>
        {
          <Form
            layout="inline"
            ref={expandRef}
            onFinish={onFinish}
          >
            <Form.Item
              label="边类型"
              name="edgeTypeName"
              style={{minWidth: '240px'}}
            >
              <Select showSearch allowClear={true} placeholder="请选择">
              {
                edgeTypeNameList?.map((item:any) => <Select.Option key={item.id} value={item.name}>{item.name}</Select.Option>)
              }
              </Select>
            </Form.Item>
            <StrengthCount
              type={[]}
              strengthType = {totalData.strengthType}
              countType = {totalData.countType}
              changeStrength={changeStrength}
              changeCount={changeCount}
              changeStrengthValue={changeStrengthValue}
              changeCountValue={changeCountValue}
              />
            <Form.Item style={{width: '80px',float:'left'}}>
              <Space style={{ margin: '0 8px 0 20px' }}>
                <Button type='primary' htmlType='submit'>查询</Button>
              </Space>
            </Form.Item>
          </Form>
        }
        {
          <Table
          bordered
          rowSelection={rowSelection}
          columns={columns}
          dataSource={tableDataList}
          showSorterTooltip={false}
          scroll={{ y: scrolly }}
          rowKey="id"
          pagination={{
            defaultPageSize: 50,
            pageSizeOptions: ['50', '100'],
            showSizeChanger: true,
            showQuickJumper: true,
            showTotal: (total) => `共 ${total} 条`
          }}
          sortDirections={['descend', 'ascend']}
          onRow={record => {
            return {onClick: () => handleClick(record)}
          }}
          />
        }

      </div>
    </>
  )
}
StatisticsPanel.displayName = 'StatisticsPanel'
export {StatisticsPanel}
