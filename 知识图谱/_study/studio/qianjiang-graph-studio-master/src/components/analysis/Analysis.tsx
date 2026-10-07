import React, {useCallback, useContext, useEffect, useRef, useState} from "react"
import {Drawer, Table} from "antd"
import {GraphinContext, GraphinData} from "@antv/graphin"
import {Algorithm} from "@antv/g6"
import  "./Analysis.scss";

// @ts-ignore
const {pageRank} = Algorithm


type props = {
  data: GraphinData,
  visible: boolean,
  setVisible: (visible: boolean) => void
}

const columns = [{
  title: '序号',
  dataIndex: 'index',
  key: 'index'
}, {
  title: '节点',
  dataIndex: 'name',
  key: 'name'
}, {
  title: '核心度',
  dataIndex: 'value',
  key: 'value'
}]

export const Analysis: React.FC<props> = ({data, visible, setVisible}) => {
  const [drawerData, setDrawerData] = useState<any>([])
  const lastId = useRef(null)
  const {graph} = useContext(GraphinContext)

  useEffect(() => {
    if (!visible) {
      return
    }
    const drawerData: any[] = []
    const pageRankResult = pageRank(data)
    const pageRankResultKeys = Object.keys(pageRankResult)
    data.nodes.forEach(node => {
      if (pageRankResultKeys.includes(node.id)&&!node.id.includes('gatherNodeId')) {
        drawerData.push({
          key: node.id,
          id: node.id,
          name: node.style?.label?.value,
          value: pageRankResult[node.id].toFixed(4)
        })
      }
    })
    drawerData.sort((a, b) => b.value - a.value).forEach((item, index) => item.index = index + 1)
    setDrawerData(drawerData)
  }, [data, visible])

  const handleClick = useCallback((record) => {
    graph.getEdges().forEach(it => {
      graph.setItemState(it, 'obvious', false)
      graph.setItemState(it, 'disabled', true)
    })
    graph.getNodes().forEach(it => {
      const model = it.getModel()
      if (model.id===record.id) {
        graph.setItemState(it, 'obvious', true)
        graph.setItemState(it, 'disabled', false)
      }else{
        graph.setItemState(it, 'obvious', false)
        setTimeout(() => {
        graph.setItemState(it, 'disabled', true)
        });
      }
    })
  }, [graph])

  const onDrawerClose = () => {
    if (lastId.current !== null) {
      graph.clearItemStates(lastId.current)
    }
    setVisible(false)
  }

  return (
    <Drawer title="核心度详情" placement="right" onClose={onDrawerClose} visible={visible} mask={false}
    style={{top: '65px',left:'auto', width: visible ? '395px' : 0}}
            maskClosable={false}>
      <Table dataSource={drawerData} columns={columns} onRow={record => {
        return {onClick: () => handleClick(record)}
      }}/>
    </Drawer>
  )
}
