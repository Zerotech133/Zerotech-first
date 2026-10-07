import React, { useEffect, useState } from "react"
import { ContextMenu as GraphinContextMenu } from '@antv/graphin-components'
import { GraphinContext, IUserEdge, IUserNode, Layout } from "@antv/graphin"
import { Button, Menu, Modal, Table } from "antd"
import { EGraphLayoutType } from "../../common"
import { IG6GraphEvent } from "@antv/g6";
import { queryEdgeApi, exportExcelApi } from '@/api/chart'
import store from "@/redux/store"
import { setRawDataVisible, setGraphData } from "@/redux/action"

type props = {
  layout: Layout & { type: EGraphLayoutType },
  onUpdateStyle: (element: IUserNode | IUserEdge, type: 'node' | 'edge') => void,
  onServerExpand: (activeNodeId: string) => void,
  onExplore: (type: string) => void,
  handleNodeTop: (nodeId: string) => void,
  setTop: (id: string) => void,
  handleUpdateFinish: () => void
  data: any,
  hideMap: any,
  getClickNodeId: (nodeId: any) => void,
  openRemoveCombo: any,
  openAddCombo: any,
  showNewEdges: (param:any,param1: string[]) => void
}

export const ContextMenu: React.FC<props> = (
  {
    layout,
    onUpdateStyle,
    onServerExpand,
    onExplore,
    handleNodeTop,
    setTop,
    handleUpdateFinish,
    data,
    hideMap,
    openRemoveCombo,
    getClickNodeId,
    openAddCombo,
    showNewEdges
  }) => {
  const [relationDialogVisible, setRelationDialogVisible] = useState(false)
  const [relatedIds, setRelatedIds] = useState<string[]>([])
  const graphin = React.useContext(GraphinContext)
  const [scrolly, setScrolly] = useState(500)
  const [lastId, setLastId] = useState('')
  const [menuType, setMenuType] = useState('')

  let comboIds: any
  useEffect(() => {
    const dom = document.querySelector('#root') as HTMLElement
    const scrolly = dom.offsetHeight - 400
    setScrolly(scrolly)
  }, [])


  const clearStates = () => {
    const graph = graphin.graph
    graph.getNodes().forEach((node) => {
      graph.clearItemStates(node)
    })
    graph.getEdges().forEach((edge) => {
      graph.clearItemStates(edge)
    })
  }

  const nodeExpand = (node: IUserNode) => {
    const graph = graphin.graph
    const id = node.getModel().id
    let tempRelatedIds: any = []
    // 选择不同点扩展，之前的状态清除，重置
    if (lastId !== id) {
      setRelatedIds([])
      clearStates()
    } else {
      tempRelatedIds = [...relatedIds]
    }
    setLastId(id)
    if (!tempRelatedIds.includes(id)) {
      tempRelatedIds.push(id)
    }
    const gatherNodesId = []
    const newShowEdges = []
    tempRelatedIds.forEach(id => {
      if (id.includes('gatherNodeId')) {
        const node = graph.findById(id)
        const maxNodeId = node.getModel().maxNodeId
        if (!tempRelatedIds.includes(maxNodeId)) {
          const expandEdgesId = []
          gatherNodesId.push(id)
          const hideEdges = hideMap[id].hideEdges
          data.edges.forEach(edge => {
            const { source, target } = edge
            if (tempRelatedIds.includes(source)&&target===id) {
              edge.target = edge.attrs['终点']
              newShowEdges.push(edge)
              expandEdgesId.push(edge.attrs['终点'])
            }
            if (tempRelatedIds.includes(target)&&source===id) {
              edge.source = edge.attrs['起点']
              newShowEdges.push(edge)
              expandEdgesId.push(edge.attrs['起点'])
            }
          })
          hideEdges.forEach(edge => {
            const { source, target } = edge
            if (expandEdgesId.includes(source) || expandEdgesId.includes(target)) {
              newShowEdges.push(edge)
            }
          })
          data.edges.forEach(edge => {
            const { source, target, attrs } = edge
            if (attrs) {
              const realSource = attrs['起点']
              const realTarget = attrs['终点']
              if (source === id&&expandEdgesId.includes(realSource) ) {
                edge.source = realSource
              } else if (target === id&&expandEdgesId.includes(realTarget) ) {
                edge.target = realTarget
              }
            }
          })
          tempRelatedIds = tempRelatedIds.concat(expandEdgesId)
        }
      }
    })
    if (newShowEdges.length) {
      showNewEdges(newShowEdges, gatherNodesId)
    }
    tempRelatedIds = tempRelatedIds.filter(item => !gatherNodesId.includes(item))
    if (gatherNodesId.length) {
      setTimeout(() => {
        highlightGraph(tempRelatedIds, gatherNodesId, graph, id)
      }, 1000)
    } else {
      highlightGraph(tempRelatedIds, gatherNodesId, graph, id)
    }



  }
  const highlightGraph = (tempRelatedIds, gatherNodesId, graph, id) => {
    const newAddIds: string[] = []
    graph.getEdges().forEach(it => {
      const model = it.getModel()
      const source = model.source  as string
      const target = model.target  as string
      if (tempRelatedIds.includes(source) && tempRelatedIds.includes(target)) {
        graph.setItemState(it, 'actived', true)
      } else if (tempRelatedIds.includes(source) && !tempRelatedIds.includes(target)) {
        if (!gatherNodesId.includes(source) && !gatherNodesId.includes(target)) {
          newAddIds.push(target)
          graph.setItemState(it, 'actived', true)
        }
      } else if (tempRelatedIds.includes(target) && !tempRelatedIds.includes(source)) {
        if (!gatherNodesId.includes(source) && !gatherNodesId.includes(target)) {
          newAddIds.push(source)
          graph.setItemState(it, 'actived', true)
        }
      } else {
        graph.setItemState(it, 'disabled', true)
      }
    })
    // 点去重
    tempRelatedIds = tempRelatedIds.concat(newAddIds)
    let setData: any = new Set(tempRelatedIds)
    tempRelatedIds = [...setData]
    graph.getNodes().forEach(it => {
      const model = it.getModel()
      if (tempRelatedIds.includes(model.id as string)) {
        if (id === it.getModel().id) {
          graph.setItemState(it, 'obvious', true)
        } else {
          graph.setItemState(it, 'actived', true)
        }
      } else {
        graph.setItemState(it, 'disabled', true)
      }
    })
    setRelatedIds(tempRelatedIds)
  }

  useEffect(() => {
    const graph = graphin.graph
    const handleClick = (evt: IG6GraphEvent) => {
      setRelatedIds([])
    }
    const menuShow = (evt: IG6GraphEvent) => {
      const type = evt.item?._cfg?.type as string
      setMenuType(type)
      setMenuType('')
    }
    graph.on('canvas:click', handleClick)
    graph.on('edge:click', handleClick)
    graph.on('node:click', handleClick)
    // 避免edge对应面板与node面板重叠
    graph.on('edge:contextmenu', menuShow)
    graph.on('node:contextmenu', menuShow)
    return () => {
      graph.off('canvas:click', handleClick)
      graph.off('edge:click', handleClick)
      graph.off('node:click', handleClick)
      graph.off('node:contextmenu', menuShow)
      graph.off('edge:contextmenu', menuShow)
    }
  }, [graphin])

  const nodeServerExpand = (node: IUserNode) => {
    onServerExpand(node.getModel().id)
  }

  const nodeCancelExpand = () => {
    clearStates()
    setRelatedIds([])
  }
  // 判断是否出现移出集合
  const getNodeCombo = (node: any) => {
    if (!node.item_cfg) return
    const graph = graphin.graph
    const selectNodeId = node.item?.getModel()?.id
    const selectDataNode = data.nodes.find((i: any) => i.id === selectNodeId)
    if (selectDataNode?.comboId?.length > 0) {
      comboIds = selectDataNode.comboId
    } else {
      let selectedNodes = graph.findAllByState('node', 'selected')
      let obviousNodes=graph.findAllByState('node', 'obvious')
      let activedNodes=graph.findAllByState('node', 'actived')
      selectedNodes=[...selectedNodes,...obviousNodes,...activedNodes]
      if (selectedNodes.length > 0) {
        let selectedNodesId: any = []
        selectedNodes.forEach((it: any) => {
          const nodeId = it.getModel().id
          selectedNodesId.push(nodeId)
        })
        data.nodes.forEach((node: any) => {
          let selectedNodesIdByData = selectedNodesId.find((i: any) => node.id === i)
          if (selectedNodesIdByData !== undefined) {
            if (node.comboId) {
              comboIds = [...node.comboId]
            }
          }
        })
      }
    }
  }
  // 获取对端
  const getOppositeNodes = (node: any) => {
    const graph = graphin.graph
    const { id, maxNodeId} = node.item.getModel()
    clearStates()
    graph.getEdges().forEach(it => {
      const model = it.getModel()
      const {source, target} = model
      if (source === id && target===maxNodeId) {
        graph.setItemState(target as string, 'selected', true)
      } else if (source===maxNodeId&&target===id) {
        graph.setItemState(source as string, 'selected', true)
      }
    })
  }
  const NodeMenu = () => {
    const node = graphin.contextmenu.node
    const flag = node?.item?._cfg?.id?.includes('gatherNodeId')

    const graph = graphin.graph
    getNodeCombo(node)
    const handleClick = ({ key }: { key: string }) => {
      if (!node.item._cfg) return node.handleClose()
      if (key === 'toFront') {// 置为顶点
        setTop(node.item.getID())
      } else if (key === 'toCenter') {
        graph.focusItem(node.item, true)
      } else if (key === 'updateStyle') {
        onUpdateStyle(node.item, 'node')
        // graph.setItemState(node.item, 'selected', true)
      } else if (key === 'expand') {
        nodeExpand(node.item)
      } else if (key === 'serverExpand') {
        nodeServerExpand(node.item)
        graph.setItemState(node.item, 'selected', true)
        // 关闭更新样式面板
        handleUpdateFinish()
      } else if (key === 'cancelExpand') {
        nodeCancelExpand()
      } else if (key === 'addCombos') {
        getClickNodeId(node.item.getModel().id)
        openAddCombo()
      } else if (key === 'removeCombos') {
        getClickNodeId(node.item.getModel().id)
        openRemoveCombo()
      } else if (key === 'oppositeNodes') {
        getOppositeNodes(node)
      }
      node.handleClose()
    }
    return (
      <Menu onClick={handleClick} style={{ width: 156, boxShadow: '0 0 11px 0 #0000001a' }} mode="vertical">
        {layout.type === EGraphLayoutType.dagre && <Menu.Item key="toFront">置为顶点</Menu.Item>}
        {<Menu.Item key="toCenter">置为中心点</Menu.Item>}
        {!flag&&<Menu.Item key="updateStyle">更新样式</Menu.Item>}
        {!flag&&<Menu.Item key="expand">本地扩展</Menu.Item>}
        {!flag&&relatedIds.length > 0 &&  <Menu.Item key="cancelExpand">取消本地扩展</Menu.Item>}
        {!flag&&<Menu.Item key="addCombos">加入集合</Menu.Item>}
        {comboIds?.length > 0 && !flag&&<Menu.Item key="removeCombos">移出集合</Menu.Item>}
        {flag&&<Menu.Item key="oppositeNodes">获取对端</Menu.Item>}
      </Menu>
    )
  }
  const EdgeMenu = () => {
    const edge = graphin.contextmenu.edge
    if (edge.item === null) {
      return <div></div>
    }
    const { source, target } = edge.item?.getModel()
    const flag = !source.includes('gatherNodeId') && !target.includes('gatherNodeId')
    const handleClick = async ({ key }: { key: string }) => {
      if (key === 'showRelationList') {
        await showOriginData(edge.item.getModel())
        setRelationDialogVisible(true)
      } else if (key === 'showData') {
        store.dispatch(setGraphData({ edgesData: [edge.item], nodesData: [] }))
        store.dispatch(setRawDataVisible({ visible: true }))
      } else {
        onUpdateStyle(edge.item, 'edge')
      }
      edge.handleClose()
    }
    return (
      <Menu onClick={handleClick} style={{ width: 156, boxShadow: '0 0 11px 0 #0000001a' }} mode="vertical">
        {flag&&<Menu.Item key="showRelationList">关系列表</Menu.Item>}
        {<Menu.Item key="updateStyle">更新样式</Menu.Item>}
        {flag&&<Menu.Item key="showData">原始数据展示</Menu.Item>}
      </Menu>
    )
  }
  const [columns, setColumns] = useState<any>([])
  const [tableData, setTableData] = useState([])
  const [exportExcelData, setExportExcelData] = useState<any>()
  const showOriginData = (edge: any) => {
    let { source, target, edgeTypeName } = edge
    let param = { 'sourceId': source, 'destinationId': target, 'edgeTypeName': edgeTypeName }
    setExportExcelData(param)
    queryEdgeApi(param).then(res => {
      if (res.data.length === 0) {
        setColumns([])
        setTableData([])
        return
      }
      // 抽取表头
      let firstData = res.data[0]
      let headers = { ...firstData.propertyList }
      delete headers.propertyList
      let propertyListColumns: any = Object.keys(headers).map((header: string) => {
        return {
          title: header,
          key: header,
          dataIndex: header
        }
      })
      const columns = [
        { title: 'id', key: 'id', dataIndex: 'id' },
        { title: '起始点', key: 'source', dataIndex: 'source' },
        { title: '目标点', key: 'destination', dataIndex: 'destination' },
        { title: '边类型', key: 'type', dataIndex: 'type' },
        { title: '强度属性', key: 'strength', dataIndex: 'strength' },
        { title: '时间属性', key: 'time', dataIndex: 'time' },
        ...propertyListColumns
      ]
      setColumns(columns)
      // 获取表格数据
      let tableData: any = []
      res.data.forEach((item: any) => {
        let obj: any = { key: item.id }
        for (let key in item) {
          if (key === 'propertyList') {
            obj = { ...obj, ...item[key] }
          } else {
            obj = { ...obj, [key]: item[key] }
          }
        }
        tableData.push(obj)
      })
      setTableData(tableData)
    })
  }
  // 导出
  const exportExcel = () => {
    exportExcelApi(exportExcelData)
  }

  return (
    <>
      <Modal
        maskClosable={false}
        title="关系列表"
        visible={relationDialogVisible}
        footer={null}
        width={1000}
        onCancel={() => setRelationDialogVisible(false)}>
        <div style={{ textAlign: 'right' }}>
          <Button type="primary" onClick={exportExcel}>导出</Button>
        </div>
        <Table
          columns={columns}
          dataSource={tableData}
          pagination={false}
          style={{ margin: '20px 0' }}
          scroll={{ x: 1500, y: scrolly }}
        />
        <div style={{ textAlign: 'center' }}><Button type="primary" onClick={() => setRelationDialogVisible(false)}>取消</Button></div>
      </Modal>
      {
        menuType !== 'edge' &&
        <GraphinContextMenu style={{ width: 156 }} bindType="node">
          <NodeMenu />
        </GraphinContextMenu>
      }
      {
        menuType !== 'node' &&
        <GraphinContextMenu style={{ width: 156 }} bindType="edge">
          <EdgeMenu />
        </GraphinContextMenu>
      }
    </>
  )
}
