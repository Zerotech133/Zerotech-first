import React, { useCallback, useEffect, useRef, useState } from 'react'
import Graphin, { Behaviors, GraphinData, IUserEdge, IUserNode, Layout, Utils } from '@antv/graphin'
import { Card, Spin, Col, Row, Statistic, AutoComplete, SelectProps, Modal, message } from 'antd'
import { Legend } from '@antv/graphin-components'
import '@antv/graphin-icons/dist/index.css'
import './ChartExplore.scss'
import {
  Analysis,
  ContextMenu,
  CustomStyle,
  Explore,
  ExploreWeb,
  Panel,
  SearchPanel,
  SearchShortestPath,
  Toolbar as CustomToolbar,
  RawData
} from '@/components'
import { SearchTextPanel } from "@/components/search-text-panel"
import { EGraphLayoutType, getPreset } from '@/common'
import { cloneDeep } from "lodash"
import { IG6GraphEvent } from "@antv/g6";
import { getSpaceEdgeList, getSpaceTagList } from '@/api/space-edit'
import { getSpaceList } from '@/api/schema'
import { taskResultAnalysisRecordApi, findPathApi, extendNodeApi, findNodeApi } from '@/api/chart'
import { setSpaceData, setNodeType, setEdgeType, setIconPath, setGraphData } from '@/redux/action/index'
import ComplicatedSearch from '@/views/chart-explore/complicated-search/ComplicatedSearch'
import SearchReturn from '@/views/chart-explore/search-return/SearchReturn'
import GraphStatistics from './graph-statistics/GraphStatistics'
import RelationTrend from './relation-trend/RelationTrend'
import SearchReturnWeb from '@/views/chart-explore/search-return-web/SearchReturnWeb'
import { ModifyCombos } from '@/components/combo/modifyCombo'
import { DeleteCombo } from '@/components/combo/deleteCombo'
import { ComboOperation } from '@/components/combo/comboOperation'
import { Combos } from "@/components/combo/combos"
import { RemoveCombos } from "@/components/combo/removeCombos"
import { showSuspectLabel, storeHideMapList } from '@/utils'
import store from "@/redux/store"
import DataRange from './data-range/DataRange'
import SameTypeReltion from './same-type-relation/SameTypeRelation'
import { querySameTypeRelationApi, relationTrendGraphApi, selectCollectionList, checkSpaceStatus } from '@/api/chart'
import { dealNodes, dealEdges, getTaskTime } from '@/utils'
import {GraphLayoutConfigMap} from '@/components/toolbar/LayoutConfig'
import HideNodesList from '@/views/chart-explore/hide-nodes-list/HideNodesList'
import mergeDataMethod from '@/utils/mergeData'
import mouseMethod from '@/utils/mouseMethod'
const { ZoomCanvas, DragNodeWithForce } = Behaviors

export const DEFAULT_LAYOUT_TYPE = EGraphLayoutType['comboCombined']
const defaultLayout = {
  type: DEFAULT_LAYOUT_TYPE,
  title: '集合布局',
  preset: {
    type: 'concentric'
  },
  preventOverlap: true
}

export const defaultNodeStyle: { [key: string]: any } = {
  size: 30,
  fill: 'transparent',
  stroke: 'gray',
  lineWidth: 1,
  opacity: 1,
  fillOpacity: 1
}

export const defaultEdgeStyle: { [key: string]: any } = {
  stroke: 'gray',
  lineWidth: 1,
  opacity: 1
}

const MAIN_STROKE_COLOR = 'red'
const NON_MAIN_STROKE_COLOR = 'gray'

type props = {
  changeSpaceId: () => void
}
let timer: any = null

const ChartExplore: React.FC<props> = ({ changeSpaceId }) => {

  const graphinRef = useRef<Graphin>(null)
  const container = useRef<HTMLDivElement>(null)
  const [selectedNodes, setSelectedNodes] = useState([])
  const [vertexIdList, setVertexIdList] = useState<any>([])//储存集合的相关内容(包括中台跳转)
  const [analysisRecordLoading, setAnalysisRecordLoading] = useState<any>(false)//全屏加载中(分析记录跳转新页面用)

  // 搜索
  const [searchVisible, setSearchVisible] = useState(false)
  const [spaceTagList, setSpaceTagList] = useState<SelectProps<object>['options']>([])
  const [spaceEdgeList, setSpaceEdgeList] = useState<SelectProps<object>['options']>([])
  const onSearchToggle = (flag: boolean) => {
    if (flag && searchTextVisible) {
      setSearchTextVisible(false)
    }
    setSearchVisible(flag)
  }

  // 查找文本
  const [searchTextVisible, setSearchTextVisible] = useState(false)
  const onSearchTextToggle = (flag: boolean) => {
    if (flag && searchVisible) {
      setSearchVisible(false)
    }
    setSearchTextVisible(flag)

  }

  // 查询图空间列表
  const [options, setOptions] = useState<SelectProps<object>['options']>()
  const [spaceName, setSpaceName] = useState(store.getState().value)
  const [iconPathList, setIconPathList] = useState<any>({})
  const [hasHref] = useState(window.location.search)
  const selectSpaceList = () => {
    getSpaceList({ pageNum: 1, pageSize: 9999 }).then((res: any) => {
      const option = res.data.list?.map((item: any) => {
        return {
          value: item.name,
          key: item.id
        }
      })
      setOptions(option)
    })
  }
  const handleSelect = (value: any, option: any) => {
    // 切换空间刷新
    if (store.getState().spaceId) {
      changeSpaceId()
    }
    let spaceId = option.key
    setSpaceName(option.value)

    store.dispatch(setSpaceData({ value: option.value, key: option.key }))

    selectSpaceTagList(spaceId)

  }

  // 分析记录数据获取
  const AnalysisRecord = () => {
    if (window.location.search.indexOf('caseId=') > -1) {
      let analysisRecordIndex=window.location.search.indexOf('analysisRecordId=')
      if (analysisRecordIndex>-1) {
        let analysisRecordId=window.location.search.slice(analysisRecordIndex).split("=")[1]
        setAnalysisRecordLoading(true)
        taskResultAnalysisRecordApi({id:analysisRecordId}).then(res=>{
          const {pathList, vertexList,edgeList} = res.data
          let endEdgeList
          if (pathList) {
            if (!vertexList.length) {
              setTimeout(() => {
                message.warn('未查询到数据')
              }, 2000);
              return
            }
            endEdgeList = [...pathList.flat(),...pathList.flat()]
          }else{
            if (!vertexList.length) {
              setTimeout(() => {
                message.warn('未查询到数据')
              }, 2000);
              return
            }
            endEdgeList=edgeList
          }
          setTimeout(() => {
            mergeData({
              edges: dealEdges(endEdgeList),
              nodes: dealNodes(vertexList),
              highlight: true
            })
          },11);
        }).finally(()=>{
          setTimeout(() => {
            setAnalysisRecordLoading(false)
          }, 2000);
        })
      }
    }
  }

  const selectSpaceTagList = (spaceId: string) => {
    getSpaceEdgeList({ spaceId }).then((res: any) => {
      setSpaceEdgeList(res.data)
      store.dispatch(setEdgeType({ edgeType: res.data }))
    })
    getSpaceTagList({ spaceId }).then((res: any) => {
      setSpaceTagList(res.data)
      let obj: any = {}
      // iconPath处理成键值对
      res.data.forEach((item: any) => {
        obj[item.name] = item.iconPath ? '/api/file' + item.iconPath : null
      })
      setIconPathList(obj)
      store.dispatch(setIconPath({ 'iconPathList': obj }))
      store.dispatch(setNodeType({ nodeType: res.data }))
    }).finally(()=>{
      AnalysisRecord()
    })
  }

  // 原始数据
  let [bindGraphData, setBindGraphData] = useState<GraphinData>({
    nodes: [],
    edges: []
  })
  // 图上显示数据(用于矫正撤销重做导致的图上数据与bindGraphData不一致)
  let [data, setData] = useState<GraphinData>({
    nodes: [],
    edges: []
  })
  let [totalData, setTotalData] = useState<any>({
    nodes: [],
    edges: []
  })

  // 不同选中样式
  const defaultStatusStyle = {
    status: {
      selected: {
        keyshape: {
          stroke: '#40a9ff'
        }
      },
      actived: {
        keyshape: {
          stroke: 'red'
        }
      },
      obvious: {
        keyshape: {
          stroke: 'red',
          size: 60,
          lineWidth: 4
        }
      }
    }
  }
  // 隐藏对端清单
  const [hideNodesVisible, setHideNodesVisible] = useState<boolean>(false)
  const [hideNodeId, setHideNodeId] = useState<string>('')
  const [hideMap, setHideMap] = useState<any>({})
  const showHideNodes = useCallback((flag:boolean) => {
    setHideNodesVisible(flag)
  },[])

  // 显示选择边跟点
  const showNewEdges = (newShowEdges, hideNodeIdParam?) => {
    const hideNodeIds = hideNodeIdParam ? hideNodeIdParam : [hideNodeId]
    const data1 = JSON.parse(JSON.stringify(data))
    const hideMap1 = JSON.parse(JSON.stringify(hideMap))
    let nodes = []
    const newHideNodes = []
    for(let hideNodeId1 in hideMap1) {
      if (!hideNodeIds.includes(hideNodeId1)) continue
      let nodesId = []
      // 中心点
      const maxNodeId = hideMap1[hideNodeId1].maxNodeId
      // 新上图点ids
      newShowEdges.forEach(item => {
        const { source, target } = item
        if (source!==maxNodeId) {
          nodesId.push(source)
        }
        if (target!==maxNodeId) {
          nodesId.push(target)
        }
      })
      let setNodesId: any = new Set(nodesId)
      nodesId = [...setNodesId]

      // 新上图点
      nodes = hideMap1[hideNodeId1].hideNodes.filter(node => nodesId.includes(node.id)).concat(nodes)
      const newHideNode = data1.nodes.find(node => node.id === hideNodeId1)

      data1.edges = data1.edges.concat(newShowEdges)
      hideMap1[hideNodeId1].hideNodes = hideMap1[hideNodeId1].hideNodes.filter(node => !nodesId.includes(node.id))
      newHideNode.hideNodesIdNum = hideMap1[hideNodeId1].hideNodes.length
      const newShowEdgesId = newShowEdges.map(edge => {
        const { source, target, edgeTypeName, strength, count } = edge
        return `${source}-${target}-${edgeTypeName}-${strength}-${count}`
      })
      hideMap1[hideNodeId1].hideEdges = hideMap1[hideNodeId1].hideEdges.filter(edge => {
        const { source, target, edgeTypeName, strength, count } = edge
        const id = `${source}-${target}-${edgeTypeName}-${strength}-${count}`
        return !newShowEdgesId.includes(id)
      })
      newHideNode.hideEdgesIdNum = hideMap1[hideNodeId1].hideEdges.length

      if (newHideNode.hideEdgesIdNum===0) {
        delete hideMap[hideNodeId1]
      }
      newHideNodes.push(newHideNode)
    }
    setHideMap(hideMap1)
    const params = {
      nodes,
      edges: newShowEdges,
      ignodeHideData: true,
      highlight: true,
      newHideNodes,
      hideMapParam: hideMap1
    }
    mergeData(params)
  }
  // 点击点或边
  const handleClickNodeEdge = useCallback((evt: IG6GraphEvent) => {
    const graph = graphinRef.current!.graph

    if ((evt.originalEvent as MouseEvent).ctrlKey) {
      if (evt.item !== null && evt.item.getType() === 'edge') {
        graph.setItemState(evt.item!, 'selected', true)
      }
      const edgesData = graph.findAllByState("edge", "selected")
      store.dispatch(setGraphData({ edgesData, nodesData: [] }))
      return
    }
    graph.getEdges().forEach((edge) => {
      graph.clearItemStates(edge)
    })
    graph.getNodes().forEach((node) => {
      graph.clearItemStates(node)
    })
    if (evt.item !== null && evt.item.getType() === 'edge') {
      graph.setItemState(evt.item!, 'selected', true)
    }
    const edgesData = graph.findAllByState("edge", "selected")
    store.dispatch(setGraphData({ edgesData, nodesData: [] }))
  }, [])
  // 双击集合点
  const handleDblClickNodeEdge = useCallback((evt: IG6GraphEvent) => {
    const nodeId = evt.item._cfg.id
    if (nodeId.indexOf('gatherNodeId')===0) {
      setHideNodeId(nodeId)
      showHideNodes(true)
    }
  },[showHideNodes])
  useEffect(() => {
    const graph = graphinRef.current!.graph
    // 默认shift框选改ctrl框选
    let timeout = setTimeout(() => {
      graph.updateBehavior('brush-select', { trigger: 'ctrl' })
    })
    graph.on('edge:click', handleClickNodeEdge)
    graph.on('node:click', handleClickNodeEdge)
    graph.on('node:dblclick', handleDblClickNodeEdge)
    return () => {
      graph.off('edge:click', handleClickNodeEdge)
      graph.off('node:click', handleClickNodeEdge)
      graph.off('node:dblclick', handleDblClickNodeEdge)
      clearTimeout(timeout)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [handleClickNodeEdge])
  // 嫌疑人标签添加tooltip
  useEffect(() => {
    const graph = graphinRef.current!.graph
    graph.addBehaviors(
      {
        type: 'tooltip',
        formatText: function formatText(model: any) {
          if (model?.attrs?.suspicionTag) {
            const suspicionTagList = model.attrs?.suspicionTag?.split(',')
            let suspectDom = ''
            suspicionTagList.forEach((item: any) => {
              suspectDom = suspectDom + `<div>${item}</div>`
            })
            const text = `<div class="suspicionTagList">${suspectDom}</div>`
            return text;
          }
          return '';
        },
        shouldBegin: (e: any) => {
          if (e.target.cfg.type === 'image') return true;
          return false;
        }
      },
      'default'
    );
    // 设置最小缩放比例至 0.001
    graph.setMinZoom(0.001)
    // 去除拖动痕迹
    graph.get('canvas').set('localRefresh', false)

    // 判断是否从中台跳转过来的
    if (window.location.search.indexOf('caseId=') > -1) {
      let port =window.location.search.indexOf('port=')
      let arr = window.location.search.split('&')
      arr.forEach(item => {
        if (item.indexOf('caseId=') > -1) {
          let spaceId = item.slice(item.indexOf('caseId=') + 7)
          store.dispatch(setSpaceData({ value: spaceId, key: spaceId }))
        }
      })
      if (port>-1) {
        checkSpaceStatus().then(res=>{
          if (res.code==="200") {
            selectCollectionList().then((res: any) => {
              let tmp: any = []
              setVertexIdList(res.data)
              res.data.forEach((element: any) => {
                tmp.push(element.name)
              });
              setAllSets(tmp)
            })
          }else{
            message.warn(res.msg)
          }
        })
      }

    } else {
      selectSpaceList()
      if (store.getState().spaceId) {
        selectSpaceTagList(store.getState().spaceId)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => {
    const graph = graphinRef.current!.graph
    let startClientX = 0,startClientY=0
    graph.on('mousedown', (e) => {
      startClientX = e.clientX
      startClientY = e.clientY
    })
    graph.on('drag', (e) => {
      const x = e.clientX - startClientX
      const y = e.clientY - startClientY
      startClientX = e.clientX
      startClientY = e.clientY
      if (e.item === null) return
      const itemStatus = e.item.getStates()

      if (itemStatus.includes('obvious') || itemStatus.includes('actived')){
        graph.getNodes().forEach((node) => {
          const model = node.getModel()
          const states = node.getStates()
          if (states.includes('obvious') || states.includes('actived')) {
            graph.updateItem(node, {
              x: model.x + x,
              y: model.y + y,
            });
          }
        })
      }
    })
  },[])
  const getNodeStyle = useCallback((node: IUserNode) => {
    return {
      ...defaultNodeStyle, ...node.style?.keyshape,
      stroke: [true, 'true', '嫌疑人'].includes(node.attrs.suspectFlag) ? (node.style?.keyshape?.stroke || MAIN_STROKE_COLOR) : (node.style?.keyshape?.stroke || NON_MAIN_STROKE_COLOR)
    }
  }, [])


  // 获取鼠标右键时所点击的节点id
  let clickNodeId: any
  const [recordClickNodeId, setRecordClickNodeId] = useState()
  const getClickNodeId = (nodeId: any) => {
    clickNodeId = nodeId
    setRecordClickNodeId(nodeId)
  }
  const openAddCombo = () => {
    const graph = graphinRef.current!.graph
    let selectedNodes = graph.findAllByState('node', 'selected')
    let obviousNodes = graph.findAllByState('node', 'obvious')
    let activedNodes = graph.findAllByState('node', 'actived')
    selectedNodes = [...selectedNodes, ...obviousNodes, ...activedNodes]
    let selectedNodesId: any = []
    selectedNodes.forEach((it: any) => {
      const itNode = it.getModel()
      selectedNodesId.push(itNode.id)
    })
    if (clickNodeId !== undefined && clickNodeId.split('').length > 0) {
      if (!(selectedNodesId.find((i: String) => i === clickNodeId))) {
        selectedNodesId.push(clickNodeId)
      }
    }

    selectedNodesId = selectedNodesId.filter(item => !item.includes('gatherNodeId'))
    if (selectedNodesId.length > 0) {
      setShowAddCombos(true)
    } else {
      message.warn("请先选择节点")
    }
  }
  // 进行集合操作之后更新节点文本
  const updateNodeLabel = (item: any) => {
    if(item?.style?.label?.value) {
      let label = item.style.label.value.split('\n')[0]
      item.style.label.value = `${label}\n${item.comboId?.join('\n')}`
    }
  }
  // 加入集合或创建集合
  const [showAddCombos, setShowAddCombos] = useState(false)
  const comboAll = (combos: any, comboName: any, type: any) => {
    const graph = graphinRef.current!.graph
    let selectedNodes = graph.findAllByState('node', 'selected')
    let obviousNodes = graph.findAllByState('node', 'obvious')
    let activedNodes = graph.findAllByState('node', 'actived')
    selectedNodes = [...selectedNodes, ...obviousNodes, ...activedNodes]
    let selectedNodesId: any = []
    selectedNodes.forEach((it: any) => {
      const node = it.getModel()
      selectedNodesId.push(node.id)
    })
    if (recordClickNodeId !== undefined) {
      if (!(selectedNodesId.find((i: String) => i === recordClickNodeId))) {
        selectedNodesId.push(recordClickNodeId)
      }
    }
    const newData = JSON.parse(JSON.stringify(data))
    newData.nodes.forEach((item: any) => {
      let selectedNodesIdByData = selectedNodesId.find((i: any) => item.id === i)
      if (selectedNodesIdByData !== undefined) {
        item.isShow=true
        if (item.comboId) {
          if (type === 'setCombos') {//创建集合
            item.comboId.push(comboName)
          } else {//加入集合
            let comboIdIndex = item.comboId.find((i: any) => i === comboName)
            if (comboIdIndex === undefined) {
              item.comboId.push(comboName)
            }
          }
        } else {
          item.comboId = []
          item.comboId.push(comboName)
        }
      }
      if (item.comboId && item.comboId.length > 0) {
        updateNodeLabel(item)
      }
    })
    setData(newData)
    setBindGraphData(newData)
    const nodes = graph.getNodes()
    const edges = graph.getEdges()
    nodes.forEach((it: any) => {
      const model = it.getModel()
      if (selectedNodesId.includes(model.id as string)) {
        graph.setItemState(it, 'actived', true)
        graph.setItemState(it, 'selected', false)
      } else {
        graph.setItemState(it, 'actived', false)
      }
    })
    edges.forEach(it => {
      graph.clearItemStates(it);
      const model = it.getModel()
      let source = selectedNodesId.find((id: any) => id === model.source)
      let target = selectedNodesId.find((id: any) => id === model.target)
      if (source !== undefined && target !== undefined) {
        graph.setItemState(it, 'actived', true)
      }
    })
  }
  useEffect(() => {
    setTimeout(() => {
      const graph = graphinRef?.current!.graph
      // 添加嫌疑人标签
      showSuspectLabel(graph)
    }, 50)
  }, [data])

  // 创建集合
  const [allSets, setAllSets] = useState<any>([])//所有集合
  const setCombos = (combos: any) => {
    const comboName = combos.combos[combos.combos.length - 1].id
    comboAll(combos, comboName, 'setCombos')
    let tmp: any = new Set([...allSets, comboName])
    setAllSets([...tmp])
  }

  // 加入集合
  const getCombo = ((combo: any) => {
    const comboName = combo.comboId
    comboAll(combo, comboName, 'getCombo')
  })
  // 移出集合
  const [showRemoveCombos, setShowRemoveCombos] = useState(false)
  const [tabelList, setTabelList] = useState<any>([])
  const [copyData, setCopy] = useState<any>([])
  // 打开移出集合弹窗
  const openRemoveCombo = () => {
    const graph = graphinRef.current!.graph
    let selectedNodes = graph.findAllByState('node', 'selected')
    let obviousNodes = graph.findAllByState('node', 'obvious')
    let activedNodes = graph.findAllByState('node', 'actived')
    selectedNodes = [...selectedNodes, ...obviousNodes, ...activedNodes]
    let selectedNodesId: any = []
    selectedNodes.forEach((it: any) => {
      const itNode = it.getModel()
      selectedNodesId.push(itNode.id)
    })
    if (clickNodeId !== undefined && clickNodeId.split('').length > 0) {
      if ((selectedNodesId.find((i: String) => i === clickNodeId)) === undefined) {
        selectedNodesId.push(clickNodeId)
      }
    }
    let transitVariable: any = []
    let allCombo: any = []
    // 存在选中集合展示选中集合
    data.nodes.forEach((node: any) => {
      // 选中的节点存在集合
      // selectedNodesId.forEach((i: any) => {
      //   if (node.id === i && node.comboId) allCombo.push(...node.comboId)
      // });
      if (node.comboId) allCombo.push(...node.comboId)
      let selectedNodesIdByData = selectedNodesId.find((i: any) => node.id === i)
      if (selectedNodesIdByData !== undefined && node.comboId?.length > 0) {
        let list = {
          id: node.id,
          comboId: node.comboId,
          label: node.style.label.value,
          key: node.id
        }
        transitVariable.push(list)
      }
    })
    // 不存在选中集合，展示全部集合
    if (transitVariable.length === 0) {
      data.nodes.forEach((node: any) => {
        if (node.comboId?.length > 0) {
          let list = {
            id: node.id,
            comboId: node.comboId,
            label: node.style.label.value,
            key: node.id
          }
          transitVariable.push(list)
        }
      })
    }
    setTabelList(transitVariable)
    if (allCombo.length > 0) {
      setTimeout(() => {
        setShowRemoveCombos(true)
        setCopy(JSON.parse(JSON.stringify(data)))
      });
    } else {
      message.warn("不存在集合，无法移出")
    }
  }
  const removeCombos = (newData: any) => {
    const dataNew = JSON.parse(JSON.stringify(newData))
    dataNew.nodes.forEach((item) => {
      updateNodeLabel(item)
      vertexIdList.forEach((element:any) => {
        let index=element.vertexIdList.findIndex((id:string)=>id===item.id)
        if (index>-1) {
          let tmp=item.comboId.find((combo:string)=>combo===element.name)
          if (tmp===undefined) {
            element.vertexIdList.splice(index,1)
            item.isShow=true
          }
        }
      });
    })
    setData(dataNew)
    setBindGraphData(dataNew)
  }
  const modifyTable = () => {
    const list = tabelList.map((item: any) => item)
    setTabelList(list)
  }
  //打开 修改集合弹窗
  const [modifyComboVisible, setModifyComboVisible] = useState(false)
  const openModifyCombo = () => {
    setModifyComboVisible(true)
  }
  // 修改集合
  const modifyCombo = (modify: any) => {
    vertexIdList.forEach((element: any) => {
      if (element.name === modify.oldComboNmae) element.name = modify.newComboName
    });
    const newData = JSON.parse(JSON.stringify(data))
    newData.nodes.forEach((item: any) => {
      if (item.comboId) {
        item.isShow=true
        let comboIdIndex = item.comboId.findIndex((i: any) => modify.oldComboNmae === i)
        if (comboIdIndex > -1) {
          item.comboId.splice(comboIdIndex, 1, modify.newComboName.trim())
          updateNodeLabel(item)
        }
      }
    })
    setData(newData)
    setBindGraphData(newData)
    let index = allSets.findIndex((item: any) => item === modify.oldComboNmae)
    let tmp = allSets
    tmp.splice(index, 1, modify.newComboName)
    setAllSets([...tmp])
  }
  // 打开删除集合弹窗
  const [deleteComboVisible, setDeleteComboVisible] = useState(false)
  const openDeleteCombo = () => {
    setDeleteComboVisible(true)
  }
  // 删除集合
  const deleteCombo = (deleteCombosList: any) => {
    const newData = JSON.parse(JSON.stringify(data))
    newData.nodes.forEach((item: any) => {
      deleteCombosList.forEach((combo: any) => {
        if (item.comboId) {
          let findComboId = item.comboId.findIndex((i: any) => {
            return combo === i
          })
          if (findComboId > -1) {
            item.isShow=true
            item.comboId.splice(findComboId, 1)
            updateNodeLabel(item)
          }
        }
      });
    })
    let copyAllSets = allSets
    deleteCombosList.forEach((combo: any) => {
      vertexIdList.forEach((element: any, index: number) => {
        if (element.name === combo) vertexIdList.splice(index,1)
      });
      let tmp = copyAllSets.filter((i: any) => combo !== i)
      copyAllSets = tmp
    });
    setAllSets([...copyAllSets])
    setData(newData)
    setBindGraphData(newData)
  }
  // 打开集合计算弹窗
  const [comboOperationVisible, setComboOperationVisible] = useState(false)
  const openComboOperation = () => {
    setComboOperationVisible(true)
  }
  // 初始化集合
  const initComno = useCallback((data) => {
    let tmp: any = []
    let copyVertexIdList: any = vertexIdList
    data.nodes.forEach((item: any) => {
      if (!item.isShow) {
        if (item.attrs?.caseInfoList) {
          let caseInfoList=JSON.parse(item.attrs.caseInfoList)
          caseInfoList.forEach(caseInfo => {
            if (!item.comboId) item.comboId=[]
            item.comboId=[...item.comboId,caseInfo.caseName]
          });
        }
      }
      if (item.comboId && item.comboId.length > 0) {
        let allComboId: any = new Set(item.comboId)
        item.comboId = [...allComboId]
        updateNodeLabel(item)
        item.comboId.forEach((id: any) => {
          tmp.push(id)
          if (copyVertexIdList.length>0) {
           let index= copyVertexIdList.findIndex((it:any)=>it.name===id)
            if (index>-1) {
              copyVertexIdList[index].vertexIdList.push(item.id)
              let list:any =new Set(copyVertexIdList[index].vertexIdList)
              copyVertexIdList[index].vertexIdList=[...list]
            }else{
              copyVertexIdList.push({
                id: copyVertexIdList.length + 1,
                name: id,
                vertexIdList: [item.id]
              })
            }
          } else {
            copyVertexIdList.push({
              id: copyVertexIdList.length + 1,
              name: id,
              vertexIdList: [item.id]
            })
          }
        });
      }
    })
    setVertexIdList(copyVertexIdList)
    let combos: any = new Set([...tmp, ...allSets])
    setAllSets([...combos])
    // 中台跳转的初始化集合
    vertexIdList.forEach((element: any) => {
      element.vertexIdList.forEach((item: any) => {
        data.nodes.forEach((node: any) => {
          if (node.id === item) {
            if (node.comboId) {
              let tmp = node.comboId.find((combo: any) => {
                return combo === element.name
              })
              if (tmp === undefined) {
                node.comboId.push(element.name)
              }
            } else {
              node.comboId = [element.name]
            }
            updateNodeLabel(node)
          }
        });
      });
    });
  }, [vertexIdList, allSets])

  // 合并图数据
  const [autoQueryFinishFlag,setAutoQueryFinishFlag]=useState(true)
  const mergeData = useCallback(({ nodes, edges, highlight, key, ignodeHideData, newHideNodes, hideMapParam }: { nodes: any[], edges: any[], highlight?: boolean, key?: string, ignodeHideData?:boolean, newHideNodes?:any, hideMapParam?:any }) => {
    // 需要高亮点边的id
    let newNodeIds = []
    if (nodes?.length) {
      newNodeIds = nodes.map(item => item.id).filter(item => !item.includes('gatherNodeId'))
    }
    let newEdgeIds: string[] = []
    if (edges?.length) {
      edges.forEach(item => {
        const { source, target } = item
        newEdgeIds.push(`${source}-${target}`)
        if (!source.includes('gatherNodeId')) {
          newNodeIds.push(source)
        }
        if (!target.includes('gatherNodeId')) {
          newNodeIds.push(target)
        }
      })
      let set: any = new Set(newEdgeIds)
      newEdgeIds = [...set]
    }
    const dataCopy = JSON.parse(JSON.stringify(data))
    // 矫正撤销导致的数据错误
    // mergeDataMethod.initCurrentData(dataCopy)

    // 多余隐藏数据过滤
    const gatherNodesId = dataCopy.nodes.filter(item => item.id.includes('gatherNodeId')).map(item => item.id)
    for(let gatherNodeId in hideMap) {
      if (!gatherNodesId.includes(gatherNodeId)) {
        delete hideMap[gatherNodeId]
      }
    }
    // 去掉集合产生的node edge，恢复原node edge，与新的接口数据拼接展示新页面
    // 粘贴和统计上图时ignodeHideData=true
    if (!ignodeHideData) {
      mergeDataMethod.initData(hideMap, dataCopy)
    } else {
      // 上图后修改隐藏点 隐藏边数值
      if (newHideNodes?.length) {
        // 与新上图点连接的其它边恢复原数据
        mergeDataMethod.recoverEdges(dataCopy, newNodeIds)
        // 隐藏点边上图
        mergeDataMethod.addHideData(dataCopy, newHideNodes)
      }
    }
    // 处理新上图的点，添加样式
    if (nodes?.length) {
      const nodeData: any = mergeDataMethod.newNodesDeal(dataCopy,nodes,getNodeStyle)
      nodes = nodeData.nodes
    } else {
      nodes = []
    }

    // 处理新上图的边，添加样式
    if (edges?.length) {
      const edgeData: any = mergeDataMethod.newEdgesDeal(dataCopy,edges,defaultEdgeStyle)
      edges = edgeData.edges
    } else {
      edges = []
    }
    const isDoubleSearch = mergeDataMethod.isDoubleSearch(hideMap,highlight)
    // 图上有集合，新上图点全部展示
    if (nodes.length || edges.length || key || isDoubleSearch) {
      // 新的数据+图上原数据
      let graphData = {
        nodes: [...dataCopy.nodes, ...nodes],
        edges: Utils.processEdges([...dataCopy.edges, ...edges], { poly: 30, loop: 10 })
      }
      if (!ignodeHideData) {
        // 连接边限制数量，超出需要做聚会
        let maxNum = 100

        // 计算每个点对应边的数量,筛选出连接边多的点
        const maxNodeIds = mergeDataMethod.nodeToEdges(graphData, maxNum)
        // 嫌疑人的点，需要展示出来
        let suspectNodesId = graphData.nodes.filter((node) => node.attrs?.suspectFlag1 === '嫌疑人').map(node => node.id)
        // 查询参数点
        const searchNodes: any = store.getState().searchNodes
        // 需要展示的点
        const nodesId = data.nodes.filter(item => !item.id.includes('gatherNodeId')).map(item => item.id)
        // 中心点，嫌疑人实体，查询实体，已上图实体，需要显示
        let showNodesId = [...maxNodeIds, ...suspectNodesId, ...searchNodes, ...nodesId]
        // 图上有集合，新上图点全部展示
        if (isDoubleSearch) {
          showNodesId = showNodesId.concat(newNodeIds)
        }
        let totalHideNodesId = mergeDataMethod.getTotalHideNodes(maxNodeIds, showNodesId, key, graphData)
        let gatherNodesIdInner = []
        const hideDataObj = {}
        maxNodeIds.forEach(maxNodeId => {
          // 筛选中心点对应隐藏点边id
          const { hideNodesId, hideEdgesId } =  mergeDataMethod.getHideNodesEdgesId(graphData,maxNodeId,maxNodeIds,totalHideNodesId,searchNodes)
          // 点已经被另一个maxNodeId关联隐藏，无需做集合,循环结束
          if (hideNodesId.length < maxNum) return

          // 筛选中心点对应隐藏点，显示点
          let {showNodes, hideNodes, maxNodeLabel} = mergeDataMethod.getHideNodes(graphData, hideNodesId, maxNodeId)

          // 定义聚合点id
          let gatherNodeId:string = mergeDataMethod.defineGatherNodeId(gatherNodesIdInner)
          gatherNodesIdInner.push(gatherNodeId)
          // 定义聚合点
          const gatherNode = mergeDataMethod.defineGatherNode(hideNodesId, hideEdgesId, maxNodeLabel, gatherNodeId,maxNodeId)
          showNodes.push(gatherNode)
          graphData.nodes = showNodes

          // 筛选中心点对应隐藏边，显示边
          const { showEdges, hideEdges } = mergeDataMethod.defineGatherEdge(graphData,hideEdgesId,hideNodesId,gatherNodeId)
          showEdges.push({source: maxNodeId, target: gatherNodeId})
          graphData.edges = showEdges
          hideDataObj[gatherNodeId] = { hideNodes, hideEdges, maxNodeId }
        })
        setHideMap(hideDataObj)
        storeHideMapList(hideDataObj, graphData)
      } else {
        storeHideMapList(hideMapParam, graphData)
      }
      initComno(graphData)
      setData(graphData)
      setBindGraphData(graphData)
    }
    if (autoQueryFinishFlag) {
      let html=document.createElement('input')
      html.id="autoQueryFinishFlag"
      html.style.display="none"
      document.body.appendChild(html)
      setAutoQueryFinishFlag(false)
    }
    // 页面渲染完后获取点
    setTimeout(() => {
      const graph = graphinRef.current!.graph
      if (highlight) {
        // 新上图点边高亮
        mergeDataMethod.highlightNodesEdges(graph,newNodeIds,newEdgeIds,ignodeHideData, isDoubleSearch)
      }
      // 未在图上的node，需要高亮居中
      if (key) {
        graph.setItemState(key, 'actived', true)
        let item = graph.findById(key)
        graph.focusItem(item, true)
      } else {
        graph.fitCenter()
      }
    }, 100)
  }, [data, getNodeStyle,autoQueryFinishFlag, initComno,hideMap])


  useEffect(() => {
    const queryMap = new Map()
    window.location.search.slice(1).split('&').forEach(item => {
      const tmp = item.split('=')
      queryMap.set(tmp[0], tmp[1])
    })
    selectSpaceTagList(queryMap.get('caseId'))

    const getIconPathList = (vertexList, edgeList) => {
      if (Object.keys(store.getState().iconPathList)?.length) {
        let nodes = dealNodes(vertexList)
        let edges = dealEdges(edgeList)
        mergeData({
          edges,
          nodes
        })
      } else {
        setTimeout(() => {
          getIconPathList(vertexList, edgeList)
        }, 100)
      }
    }
    if (queryMap.has('conditionType') && queryMap.has('value')) {
      const taskName="关系净流向-"+getTaskTime()

      const param = {
        edgeTypeName: "交易信息",
        taskName,
        strengthCondition: {
          conditionType: decodeURI(queryMap.get('conditionType')),
          value: Number(queryMap.get('value'))
        }
      }
      relationTrendGraphApi(param)
        .then((res: any) => {
          getIconPathList(res.data.vertexList, res.data.edgeList)
          setLayoutType('流程图布局', 'LR')
        })
    }
    // 同类节点关系自动查询
    if (queryMap.has('function') && queryMap.get('function') === 'sameTagRelation') {
      const taskName="同类节点关系-"+getTaskTime()

      querySameTypeRelationApi({ tagName: decodeURI(queryMap.get('tagName')), maxStep: Number(queryMap.get('maxStep')),taskName })
        .then((res: any) => {
          getIconPathList(res.data.vertexList, res.data.edgeList)
          setLayoutType('集合布局')
        })
      }
    // 关系净流向自动查询
    if (queryMap.has('function') && queryMap.get('function') === 'flowDirection') {
      const taskName="关系净流向-"+getTaskTime()
      const param = {
        edgeTypeName: decodeURI(queryMap.get('edgeTypeName')),
        taskName,
        strengthCondition: {
          conditionType: decodeURI(queryMap.get('strengthConditionType')),
          value: Number(queryMap.get('strengthConditionValue'))
        }
      }
      relationTrendGraphApi(param)
        .then((res: any) => {
          getIconPathList(res.data.vertexList, res.data.edgeList)
          setLayoutType('流程图布局', 'LR')
        })
    }
    // 拓展多个账号自动查询
    if (queryMap.has('function') && queryMap.get('function') === 'extendData') {
      let param: any = {
        maxStep: 1,
        minStep: 1,
        extendDirection: "both",
        edgeTypeNameList: ["通话","手机充值","交易信息"],
        startVertexIdList: decodeURI(queryMap.get('account')).split(',')
      }
      extendNodeApi(param).then((res: any) => {
        if (!res.data) {
          message.warn('未查询到扩展数据')
          return
        }
        const { edgeList, vertexList } = res.data
        if (!edgeList.length && !vertexList.length) {
          message.warn('未查询到扩展数据')
          return
        }
        mergeData({
          edges: dealEdges(edgeList),
          nodes: dealNodes(vertexList),
          highlight: true
        })
      })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const graph = graphinRef.current!.graph
    const getActiveNodes = (state: string) => {
      let selectedNodes = graph.findAllByState('node', state)
      let selectedEdges = graph.findAllByState('edge', state)
      let edgesId = selectedEdges.map(item => {
        let model = item.getModel()
        return String(model.source) + String(model.target) + model.count + model.strength
      })
      let nodes = []
      data.nodes.forEach(item => {
        selectedNodes.forEach(ele => {
          const model = ele.getModel()
          if (item.id === model.id) {
            nodes.push({...item, oldX: model.x, oldY: model.y})
          }
        })
      })
      let edges: any = data.edges.filter((item: any) => edgesId.includes(item.source + item.target + item.count + item.strength))
      return { nodes, edges }
    }
    const keyEvent = (e: any) => {
      let doms: any = document.getElementsByClassName('ant-modal-wrap')
      doms = Array.from(doms)
      let num:number = 0
      doms.forEach(dom => {
        if (dom.style.display!=='none') num++
      })
      // 有弹框不能操作
      if (num || !e.ctrlKey || (e.target.className&&e.target.className!=='appContainer')) return
      if (e.code === 'KeyC') {
        let { nodes: nodes1, edges: edges1 } = getActiveNodes('selected')
        let { nodes: nodes2, edges: edges2 } = getActiveNodes('actived')
        let { nodes: nodes3, edges: edges3 } = getActiveNodes('obvious')
        // 扩展的actived样式复制的时候需要保存
        nodes2 = nodes2.map((item: any) => {
          return { ...item, states: ['actived'] }
        })
        edges2 = edges2.map((item: any) => {
          return { ...item, states: ['actived'] }
        })
        // 查询参数obvious状态保存
        nodes3 = nodes3.map((item: any) => {
          return { ...item, states: ['obvious'] }
        })
        edges3 = edges3.map((item: any) => {
          return { ...item, states: ['obvious'] }
        })
        // selected状态的点边需要去重actived  obvious状态
        const nodes2Id = [...nodes2, ...nodes3].map((item: any) => item.id)
        const edges2Id = [...edges2, ...edges3].map((item: any) => item.source + '-' + item.target + '-' + item.count + '-' + item.strength)
        nodes1 = nodes1.filter((item: any) => {
          return nodes2Id.indexOf(item.id) === -1
        })
        edges1 = edges1.filter((item: any) => {
          return edges2Id.indexOf(item.source + '-' + item.target + '-' + item.count + '-' + item.strength) === -1
        })

        let nodes = [...nodes1, ...nodes2, ...nodes3]
        let edges = [...edges1, ...edges2, ...edges3]
        let nodesId = nodes.map((item: any) => item.id)
        // 没有选中target 与source节点的边不显示
        edges = edges.filter((item: any) => nodesId.includes(item.source) && nodesId.includes(item.target))
        // 没有复制对端的集合点，需要把多余数据清除
        let data = mouseMethod.ctrlCMethod(nodes, edges, hideMap)
        // 没有复制到的集合点，对应hidemap不需要复制，避免数据量太大
        const gatherNodesId = data.nodes.map(item => item.id).filter(id => id.includes('gatherNodeId'))
        const hideMap1 = {}
        for(let gatherNodeId in hideMap) {
          if (gatherNodesId.includes(gatherNodeId)) {
            hideMap1[gatherNodeId] = hideMap[gatherNodeId]
          }
        }
        try {
          localStorage.setItem('hideMap', JSON.stringify(hideMap1))
          localStorage.setItem('nodesData', JSON.stringify(data))
        } catch (error) {
          message.warning('数据量过大，不支持复制')
        }
        return
      }
      // && e.path.length === 4
      if (e.code === 'KeyV') {
        let nodesData = localStorage.getItem('nodesData')
        if (nodesData) {
          const { nodes, edges, hideMapStorage } =  mouseMethod.ctrlVMethod(nodesData,data)
          let hideMapTotal = hideMap
          if (hideMapStorage) {
            hideMapTotal = {...hideMap, ...hideMapStorage}
            setHideMap(hideMapTotal)
          }
          const graph = graphinRef.current!.graph
          mergeData({
            nodes,
            edges,
            highlight: false,
            ignodeHideData: true,
            hideMapParam: hideMapTotal
          })
          nodes.forEach((node: any) => {
            node?.states?.includes('actived') && graph.setItemState(node.id, 'actived', true)
            node?.states?.includes('obvious') && graph.setItemState(node.id, 'obvious', true)
          })
          graph.getEdges().forEach((edge: any) => {
            edge.getModel().states?.includes('actived') && graph.setItemState(edge.getID(), 'actived', true)
            edge.getModel().states?.includes('obvious') && graph.setItemState(edge.getID(), 'obvious', true)
          })
          setTimeout(()=> {
            graph.getNodes().forEach((node:any) => {
              const model = node.getModel()
              graph.updateItem(node, {
                x: model.oldX,
                y: model.oldY,
              });
            })
            graph.fitCenter()
          },500)
        }
        // 粘贴1次后删除
        localStorage.removeItem('nodesData')
        localStorage.removeItem('hideMap')
        return
      }
      if (e.code === 'KeyE') {
        e.preventDefault()
        const graph = graphinRef.current!.graph
        graph.getNodes().forEach((node) => {
          graph.setItemState(node, 'selected', true)
        })
        return
      }
      if (e.code === 'KeyL') {
        e.preventDefault()
        const graph = graphinRef.current!.graph
        graph.getEdges().forEach((edge) => {
          graph.setItemState(edge, 'selected', true)
        })
        return
      }
      if (e.code === 'KeyD') {
        e.preventDefault()
        if (timer) clearTimeout(timer)
        timer = setTimeout(() => {
          mouseMethod.handelRemoveNode(graph,setHideMap,setData,setBindGraphData,hideMap,data)
        }, 200)
      }
    }
    window.addEventListener('keydown', keyEvent)


    return () => {
      window.removeEventListener('keydown', keyEvent)
      clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mergeData, data])


  // 清空数据
  const handleClearData = useCallback(() => {
    setHideMap({})
    setData({
      nodes: [],
      edges: []
    })
    setBindGraphData({
      nodes: [],
      edges: []
    })
    storeHideMapList({},{})
  }, [])

  // 最短路径
  const pathSearchRef = useRef<any>(null)

  // 布局
  const [layout, setLayout] = useState<Layout & { type: EGraphLayoutType }>(
    defaultLayout
  )
  // 设置图类型
  const setLayoutType = useCallback((type: EGraphLayoutType | string, rankdir?: string) => {
    if (String(type) === '流程图布局') rankdir = "LR"
    setTimeout(() => {
      setLayout({
        ...defaultLayout,
        type: GraphLayoutConfigMap.get(type),
        title: type,
        rankdir,
        preset: getPreset(type)
      })
    })
  }, [])

  const handleLayoutChange = (type: EGraphLayoutType,rankdir?:string) => {
    setLayout({
      ...defaultLayout,
      type: GraphLayoutConfigMap.get(type),
      title: type,
      rankdir,
      preset: getPreset(type)
    })
    // 切换布局，把层次布局改过的边soure target还原
    if (type !== 'dagre') {
      data.edges.forEach((item: any) => {
        let { source, target } = item
        if (item.sortChanged) {
          item.source = target
          item.target = source
          item.sortChanged = false
          item.newSortChanged = false
          item.style.keyshape = { ...item?.style?.keyshape, startArrow: true, endArrow: false }
        }
      })
      let currentData = { nodes: data.nodes, edges: data.edges }
      setData(currentData)
      setBindGraphData(currentData)
      storeHideMapList(hideMap, currentData)
    }
    const graph = graphinRef.current!.graph
    setTimeout(() => {
      graph.fitCenter()
    }, 500)
  }
  // 被选中高亮的node集合
  const [activeNodes, setActiveNodes] = useState<object[]>([])
  const [activeNodesId, setActiveNodesId] = useState<string[]>([])
  // 被选中高亮的edge集合
  const [activeEdges, setActiveEdges] = useState<object[]>([])
  const [activeEdgesId, setActiveEdgesId] = useState<string[]>([])
  // 打开更新样式面板时，拓展统计面板关闭
  const [num, setNum] = useState<number>(0)

  // 右键选中更新样式
  const handleUpdateStyle = (element: any, type: 'node' | 'edge') => {
    setNum(num + 1)
    setCustomStyleNode(element)
    setType(type)
    const graph = graphinRef.current!.graph
    // 选中的node集合
    let selectedNodes = graph.findAllByState('node', 'selected')
    let selectedEdges = graph.findAllByState('edge', 'selected')
    // 打开面板的触发点未高亮，也需要更新样式
    if (type === 'node') {
      let index = selectedNodes.findIndex((item: any) => {
        return item.getID() === element.getID()
      })
      if (index === -1) {
        selectedNodes.unshift(element)
      }
    } else {
      let index = selectedEdges.findIndex((item: any) => {
        return item.getID() === element.getID()
      })
      if (index === -1) {
        selectedEdges.unshift(element)
      }
    }
    // 备份选中node的原始样式
    let activedNodesList = selectedNodes.map(item => {
      return {
        id: item.getID(),
        // 更新面板操作前node的样式
        originKeyshape: { ...item.getModel().style?.keyshape }
      }
    })
    setActiveNodesId(selectedNodes.map(item => item.getID()))
    setActiveNodes(activedNodesList)
    // 更新面板显示时，取消选中node的selected状态
    // activedNodesList.forEach((node:any) => {
    //   graph.setItemState(node.id, 'selected', false);
    // })

    // 备份选中edge的原始样式
    let activedEdgesList = selectedEdges.map(item => {
      return {
        id: item.getID(),
        source: item.getModel().source,
        target: item.getModel().target,
        originKeyshape: { ...item.getModel().style?.keyshape }
      }
    })
    setActiveEdgesId(selectedEdges.map(item => item.getID()))
    setActiveEdges(activedEdgesList)
    // 更新样式面板显示时，取消选中edge的selected状态
    // activedEdgesList.forEach((edge:any) => {
    //   graph.setItemState(edge.id, 'selected', false);
    // })

    // 更新样式面板Node部分的样式参照节点
    if (type === 'node') {
      setCustomNodeElement(element)
      if (selectedEdges.length) {
        setCustomEdgeElement(selectedEdges[0])
      }
    } else {// 更新样式面板Edge部分的样式参照节点
      setCustomEdgeElement(element)
      if (selectedNodes.length) {
        setCustomNodeElement(selectedNodes[0])
      }
    }
  }
  const handleNodeTop = (nodeId: string) => {
    const temp = cloneDeep(data)
    temp.nodes.forEach(node => delete node.layer)
    const node = temp.nodes.find(it => it.id === nodeId)
    if (node) {
      node.layer = 0
      setData(temp)
      setBindGraphData(temp)
      storeHideMapList(hideMap, temp)
    }
  }

  // 样式更新
  // 更新样式面板打开的触发点
  const [customStyleElement, setCustomStyleNode] = useState<IUserNode | IUserEdge | null>(null)
  // 更新面板Node部分的样式参照节点
  const [customNodeElement, setCustomNodeElement] = useState<any>(null)
  // 更新面板Edge部分的样式参照节点
  const [customEdgeElement, setCustomEdgeElement] = useState<any>(null)
  // 更新样式面板打开的触发点类型
  const [type, setType] = useState<'node' | 'edge'>('node')

  const handleUpdateFinish = () => {
    setCustomStyleNode(null)
    setCustomNodeElement(null)
    setCustomEdgeElement(null)
  }
  // 操作更新面板，选中的node样式同步更新
  const handleUpdate = (style: any, activeTabKey: string) => {
    const graph = graphinRef.current!.graph
    let {nodes, edges} = JSON.parse(JSON.stringify(data))
    if (activeTabKey === 'node') {
      let  allNodesId=graph.getNodes().map(item=>item.getModel().id)
      activeNodes.forEach((node: any) => {
        if (allNodesId.includes(node.id)) {
          graph.updateItem(node.id, {
            style: {
              // @ts-ignore
              keyshape: { ...style }
            }
          })
          nodes.forEach((node1: any) => {
            if (node1.id === node.id) {
              node1.style.keyshape = { ...style }
            }
          })
        }
      })
    } else {
      let  allEdgesId=graph.getEdges().map(item=>item.getModel().id)
      // 避免箭头转变方向
      graph.edge((edge: any) => {
        return edge
      })
      activeEdges.forEach((edge: any) => {
        if (allEdgesId.includes(edge.id)) {
          graph.updateItem(edge.id, {
            style: {
              // @ts-ignore
              keyshape: { ...style }
            }
          })
          edges.forEach((edge1: any) => {
            if (edge1.id === edge.id) {
              edge1.style.keyshape = { ...style }
            }
          })
        }
      })
    }
    setData({ nodes, edges })
    setBindGraphData({ nodes, edges })
    storeHideMapList(hideMap, { nodes, edges })
  }

  // 探索
  const [exploreVisible, setExploreVisible] = useState(false)
  // 判断探索类型是接口查询还是图上计算
  const [exploreType, setExploreType] = useState('explore')
  const openExplore = (type: string, fn: string) => {
    setExploreType(type)
    setActiveKey(fn)
    sethistoryData(undefined)
    setExploreVisible(true)
  }
  const handleExploreClose = () => {
    setExploreVisible(false)
  }

  // 查找路径/子图
  const [activeKey, setActiveKey] = useState('path')
  const handleExplore = useCallback((type: string) => {
    setExploreVisible(true)
    setActiveKey(type)
  }, [])

  // 分析
  const [analysisVisible, setAnalysisVisible] = useState(false)
  const openAnalysis = () => {
    setAnalysisVisible(true)
    setNum(num + 1)
  }

  // 服务端扩展
  const [expandNodeId, setExpandNodeId] = useState('')
  const panelRef = useRef<any>(null)
  const handleServerExpand = (expandNodeId: string) => {
    setExpandNodeId(expandNodeId)
    panelRef.current.toggleRight(true)
  }

  const onSearchPathToggle = useCallback((flag: boolean) => {
    return pathSearchRef.current.onSearchPathToggle(flag)
  }, [])
  // 置为顶点时，已经遍历过的边,避免出现死循环
  let edgeArr: any = []

  const changeEdge = (nodes: any, edges: any, arr: any, edgeArr: any, layer: number, id?: string) => {
    let arr1: any = [] // 下级node

    edges.forEach((item: any) => {
      let { source, target } = item
      if (source === id || (arr.includes(source) && edgeArr.indexOf(source + ',' + target) === -1)) {
        arr1.push(item.target)
        edgeArr.push(source + ',' + target)
      }
      if (target === id || (arr.includes(target) && edgeArr.indexOf(source + ',' + target) === -1)) {
        arr1.push(item.source)
        edgeArr.push(source + ',' + target)
        // edge的source target需要修改
        item.newSortChanged = true
      }
    })
    nodes.forEach((item: any) => {
      if (arr1.indexOf(item.id) > -1) {
        item.layer = layer + 1
      }
    })
    if (arr1.length === 0) return
    changeEdge(nodes, edges, arr1, edgeArr, layer + 1)
  }

  // 置为顶点
  const setTop = (id: string) => {
    let newData = JSON.parse(JSON.stringify(data))
    let nodes = newData.nodes
    let edges = newData.edges
    // 恢复修改过的edge source target
    edges.forEach((item: any) => {
      let { source, target } = item
      if (item.sortChanged) {
        item.source = target
        item.target = source
        item.sortChanged = false
        item.newSortChanged = false
        item.style.keyshape = { ...item.style.keyshape, startArrow: false, endArrow: true }
      }
    })

    // 设置顶点node layer
    nodes.forEach((item: any) => {
      if (item.id === id) {
        item.layer = 0
      }
    })
    // 设置layer
    changeEdge(nodes, edges, [], edgeArr, 0, id)

    edges.forEach((item: any) => {
      let { source, target } = item
      if (item.newSortChanged) {
        // edge的起点layer<终点layer,部分edge的source与target需要对调
        item.source = target
        item.target = source
        item.sortChanged = true
        item.style.keyshape = { ...item.style.keyshape, startArrow: true, endArrow: false }
      } else {
        item.sortChanged = false
      }
    })
    let currentData = { nodes, edges: Utils.processEdges([...edges], { poly: 30, loop: 30 }) }
    setData(currentData)
    setBindGraphData(currentData)
    storeHideMapList(hideMap, currentData)
  }
  const spaceDom = <AutoComplete
    options={options}
    style={{ minWidth: '200px' }}
    defaultValue={spaceName}
    dropdownMatchSelectWidth={true}
    placeholder="请选择图空间"
    filterOption={(inputValue, option) =>
      String(option?.value).toUpperCase().indexOf(inputValue.toUpperCase()) !== -1
    }
    onSelect={handleSelect}
  />
  // 复杂查询
  const [complicatedSearchVisible, setComplicatedSearchVisible] = useState(false)
  const showComplicatedSearch = (flag: boolean) => {
    setComplicatedSearchVisible(flag)
  }

  // 查找回路
  const [searchReturnVisible, setSearchReturnVisible] = useState(false)
  const showSearchReturn = (flag: boolean) => {
    const graph = graphinRef.current!.graph
    const selectedNodes: any = graph.findAllByState('node', 'selected')
    setSelectedNodes(selectedNodes)
    setSearchReturnVisible(flag)
  }
  // 查找回路web
  const [searchReturnWebVisible, setSearchReturnWebVisible] = useState(false)
  const showSearchReturnWeb = (flag: boolean) => {
    const graph = graphinRef.current!.graph
    const selectedNodes: any = graph.findAllByState('node', 'selected')
    if (selectedNodes.length === 0 && flag) {
      message.warn('请选择节点(使用Ctrl按键)')
      return
    }
    setSelectedNodes(selectedNodes)
    setSearchReturnWebVisible(flag)
  }
  // 图谱统计
  const [graphStatisticsVisible, setGraphStatisticsVisible] = useState(false)
  const showGraphStatistics = (flag: boolean) => {
    setGraphStatisticsVisible(flag)
  }
  // 另存为
  const [saveGraphNum, setSaveGraphNum] = useState(0)
  // 关系净流向
  const [relationTrendVisible, setRelationTrendVisible] = useState(false)
  const showRelationTrend = (flag: boolean) => {
    setRelationTrendVisible(flag)
  }
  // 数据范围
  const [dataRangeVisible, setDataRangeVisible] = useState(false)
  const showDataRange = (flag: boolean) => {
    setDataRangeVisible(flag)
  }
  // 同类节点关系
  const [sameTypeRelationVisbile, setSameTypeRelationVisbile] = useState(false)
  const showSameTypeRelation = (flag: boolean) => {
    setSameTypeRelationVisbile(flag)
  }
  // 历史记录
  const [historyData, sethistoryData] = useState<any>()
  // 点击历史记录，显示对应查询面板
  const showDefaultPanel = (item: any) => {
    sethistoryData(item)
    switch (item.type) {
      case 'general': // 查询
        setSearchTextVisible(false)
        setSearchVisible(true)
        break;
      case 'text': // 查找文本
        setSearchVisible(false)
        setSearchTextVisible(true)
        break;
      case 'statistics': // 图谱统计
        setGraphStatisticsVisible(true)
        break;
      case 'findLoop': // 查找回路
        setSearchReturnVisible(true)
        break;
      case 'flowDirection': // 关系净流向
        setRelationTrendVisible(true)
        break;
      case 'findPath': // 探索-查找路径
        setExploreVisible(true)
        setActiveKey('path')
        break;
      case 'findSub': // 探索-查找路径
        setExploreVisible(true)
        setActiveKey('graph')
        break;
      case 'complex': // 复杂查询
        setComplicatedSearchVisible(true)
        break;
      case 'searchSameTagRelation': // 同类关系节点
        setSameTypeRelationVisbile(true)
      break;
    }
  }
  const [nodesLength, setNodesLength] = useState<number>(0)
  const [edgesLength, setEdgesLength] = useState<number>(0)
  useEffect(() => {
    const nodeLength1 = data.nodes.filter(item => !item.id.includes('gatherNode')).length
    const gatherNodesId: any = Object.keys(hideMap)
    const edgesLength1 = data.edges.filter((item: any) => {
      const { source, target } = item
      // 过滤中心点+集合点的边
      if (gatherNodesId.includes(source)&&target===hideMap[source].maxNodeId) {
        return false
      }
      if (gatherNodesId.includes(target)&&source===hideMap[target].maxNodeId) {
        return false
      }
      return true
    }).length
    setNodesLength(nodeLength1)
    setEdgesLength(edgesLength1)
    const datas = mouseMethod.getGraphData(data, hideMap)
    setTotalData(datas)
  }, [data, hideMap])
  // 叶影涉烟跳转过来的代码
  useEffect(() => {
    if (spaceEdgeList.length === 0) {
      // 避免因mergeData函数 死循环
      sessionStorage.setItem('smokeIndex', '0')
      return
    }
    if (sessionStorage.getItem('smokeIndex') !== '0') return
    sessionStorage.setItem('smokeIndex', '1')
    if (window.location.search.indexOf('caseId=') > -1) {
      let arr = window.location.search.split('&')

      arr.forEach((item, index) => {
        // 叶影-涉烟犯罪模型跳转过来
        if (item.indexOf('mainOppositeNumber=') > -1) {
          let mainOppositeNumber = item.slice(item.indexOf('mainOppositeNumber=') + 19).split(',')
          const edgeTypeNameList = spaceEdgeList.map(item => item.name)
          let edgeList: any = []
          let vertexList: any = []
          let finishIndex = 0
          for (let i = 0; i < mainOppositeNumber.length; i++) {
            const element = mainOppositeNumber[i]
            const taskName="查找路径-"+getTaskTime()
            const arr = element.split('-')
            Promise.all([findNodeApi({keyword: arr[0], tagName: '手机号'}), findNodeApi({keyword: arr[1], tagName: '手机号'})])
            // eslint-disable-next-line no-loop-func
            .then((res: any) => {
              const mainData = res[0].data[0]
              const oppositeData = res[1].data[0]
              const startVertexName = {
                iconPath: mainData.tag.iconPath,
                name: mainData.name
              }
              const endVertexName = {
                iconPath: oppositeData.tag.iconPath,
                name: oppositeData.name
              }
              const param = {
                direction: 'undirected',
                maxStep: 1,
                searchPathType: 'shortest',
                total: 20,
                edgeTypeNameList,
                taskName,
                startVertexId: {id: mainData.id, name: JSON.stringify(startVertexName)},
                endVertexId: {id: oppositeData.id, name: JSON.stringify(endVertexName)}
              }
              findPathApi(param).then((res: any) => {
                finishIndex ++
                edgeList = edgeList.concat([...res.data.pathList.flat()])
                vertexList = vertexList.concat(res.data?.vertexList)
                if (finishIndex === mainOppositeNumber.length) {
                  const startVertexIdList = vertexList.map(item => item.id)
                  let param: any = {
                    maxStep: 1,
                    minStep: 1,
                    extendDirection: "both",
                    edgeTypeNameList,
                    startVertexIdList
                  }
                  extendNodeApi(param).then((res: any) => {
                    if (!res.data) {
                      message.warn('未查询到扩展数据')
                      return
                    }
                    if (!res.data.edgeList.length && !res.data.vertexList.length) {
                      message.warn('未查询到扩展数据')
                      return
                    }
                    mergeData({
                      edges: dealEdges(res.data.edgeList),
                      nodes: dealNodes(res.data.vertexList),
                      highlight: true
                    })
                  })
                }
              })
            })
          }
        }
      })
    }
  }, [spaceEdgeList, mergeData])
  return (
    <>
      <Card
        className="chartExploreContainer"
        extra={(!hasHref && spaceName && spaceDom) || <div style={{ height: '32px' }}></div>}>
        {
          <Modal
            className="chartExploreModal"
            centered
            maskClosable={false}
            visible={!hasHref && !spaceName}
            closable={false}
            footer={null}
          >
            {spaceDom}
          </Modal>
        }

        <div ref={container} className="appContainer" tabIndex={-1} >
          <Graphin
            enabledStack={true} data={bindGraphData} layout={layout} ref={graphinRef}
            nodeStateStyles={defaultStatusStyle}
            edgeStateStyles={defaultStatusStyle}
          >
            <Legend bindType="node" sortKey="attrs.suspectFlag1" colorKey="style.keyshape.stroke" style={{ right: 30 }}>
              <Legend.Node />
            </Legend>
            {/* 搜索面板 */}
            <SearchPanel
              data={data}
              mergeData={mergeData}
              visible={searchVisible}
              spaceTagList={spaceTagList}
              iconPathList={iconPathList}
              historyData={historyData}
            />
            {/* 探索按钮弹框 */}
            <Explore
              visible={exploreVisible && exploreType === 'explore'}
              onExploreClose={handleExploreClose}
              mergeData={mergeData}
              activeKey={activeKey}
              iconPathList={iconPathList}
              setVisible={setExploreVisible}
              edgeTypeNameList={spaceEdgeList}
              historyData={historyData} />
            {/* 探索按钮弹框web */}
            <ExploreWeb
              visible={exploreVisible && exploreType === 'exploreWeb'}
              data={totalData}
              onExploreClose={handleExploreClose}
              mergeData={mergeData}
              activeKey={activeKey}
              iconPathList={iconPathList}
              setVisible={setExploreVisible}
              edgeTypeNameList={spaceEdgeList} />
            {/* 分析按钮-核心度详情 */}
            <Analysis visible={analysisVisible} setVisible={setAnalysisVisible} data={data} />
            {/* 详细信息面板+拓展面板 */}
            <Panel
              ref={panelRef}
              data={data}
              totalGraphData={totalData}
              activeId={expandNodeId}
              mergeData={mergeData}
              edgeTypeNameList={spaceEdgeList}
              num={num}
              saveGraphNum={saveGraphNum}
              hideMap={hideMap}
              handleUpdateFinish={handleUpdateFinish}
              showDefaultPanel={showDefaultPanel} />
            {/* 图节点边线右键显示面板 */}
            <ContextMenu
              onUpdateStyle={handleUpdateStyle} onServerExpand={handleServerExpand}
              onExplore={handleExplore}
              handleNodeTop={handleNodeTop} layout={layout} setTop={setTop}
              handleUpdateFinish={handleUpdateFinish}
              data={data}
              hideMap={hideMap}
              getClickNodeId={getClickNodeId}
              openRemoveCombo={openRemoveCombo}
              openAddCombo={openAddCombo}
              showNewEdges={showNewEdges}
            />
            <RawData />
            <ZoomCanvas />
            <DragNodeWithForce autoPin={true} />
            {/* 更新样式弹框 */}
            <CustomStyle
              type={type}
              element={customStyleElement}
              customNodeElement={customNodeElement}
              customEdgeElement={customEdgeElement}
              activeNodes={activeNodes}
              activeEdges={activeEdges}
              onUpdateFinish={handleUpdateFinish}
              onUpdate={handleUpdate} />
            {/* 创建集合弹窗 */}
            <Combos
              getCombo={getCombo}
              setCombos={setCombos}
              data={data}
              visible={showAddCombos}
              combosList={allSets}
              setVisible={setShowAddCombos}
            ></Combos>
            {/* 移出集合弹窗 */}
            <RemoveCombos
              visible={showRemoveCombos}
              setVisible={setShowRemoveCombos}
              data={data}
              copyData={copyData}
              removeTableList={tabelList}
              modifyTable={modifyTable}
              removeCombos={removeCombos}
            ></RemoveCombos>
            {/* 修改集合 */}
            <ModifyCombos
              visible={modifyComboVisible}
              setVisible={setModifyComboVisible}
              data={data}
              modifyCombo={modifyCombo}
              combosList={allSets}
            ></ModifyCombos>
            {/* 删除集合 */}
            <DeleteCombo
              visible={deleteComboVisible}
              data={data}
              combosList={allSets}
              setVisible={setDeleteComboVisible}
              deleteCombo={deleteCombo}
            ></DeleteCombo>
            {/* 集合计算 */}
            <ComboOperation
              visible={comboOperationVisible}
              setVisible={setComboOperationVisible}
              data={data}
              combosList={allSets}
            />
            {/* 点击CustomToolbar查询最短路径按钮 显示弹框*/}
            <SearchShortestPath data={totalData} mergeData={mergeData} ref={pathSearchRef} />
            {/* toolbar工具栏 */}
            <CustomToolbar
              container={container.current}
              layoutType={layout.title}
              setLayoutType={setLayoutType}
              onSearchToggle={onSearchToggle}
              onSearchTextToggle={onSearchTextToggle}
              onSearchPathToggle={onSearchPathToggle}
              onClearData={handleClearData}
              onLayoutChange={handleLayoutChange}
              data={data}
              onUpdateStyle={handleUpdateStyle}
              activeNodesId={activeNodesId}
              activeEdgesId={activeEdgesId}
              setData={setData}
              setBindGraphData={setBindGraphData}
              saveGraphNum={saveGraphNum}
              setSaveGraphNum={setSaveGraphNum}
              showRelationTrend={showRelationTrend}
              openExplore={openExplore}
              openAnalysis={openAnalysis}
              showComplicatedSearch={showComplicatedSearch}
              showSearchReturn={showSearchReturn}
              showGraphStatistics={showGraphStatistics}
              showSearchReturnWeb={showSearchReturnWeb}
              openModifyCombo={openModifyCombo}
              openDeleteCombo={openDeleteCombo}
              openRemoveCombo={openRemoveCombo}
              openAddCombo={openAddCombo}
              openComboOperation={openComboOperation}
              getClickNodeId={getClickNodeId}
              showDataRange={showDataRange}
              showSameTypeRelation={showSameTypeRelation}
              hideMap={hideMap}
              setHideMap={setHideMap}
            />
            {/* 查找回路web */}
            <SearchReturnWeb
              mergeData={mergeData}
              data={totalData}
              visible={searchReturnWebVisible}
              selectedNodes={selectedNodes}
              openModal={showSearchReturnWeb}
              edgeTypeNameList={spaceEdgeList} />
          </Graphin>
          <div style={{ position: 'absolute', left: '50px', bottom: 0 }}>
            <Row gutter={16}>
              <Col span={12}>
                <Statistic title="Node" value={nodesLength} />
              </Col>
              <Col span={12}>
                <Statistic title="Edge" value={edgesLength} />
              </Col>
            </Row>
          </div>
        </div>

      </Card>
      {/* 复杂查询 */}
      <ComplicatedSearch
        visible={complicatedSearchVisible}
        historyData={historyData}
        openModal={showComplicatedSearch}
        mergeData={mergeData}
      />
      {/* 查找回路 */}
      <SearchReturn
        mergeData={mergeData}
        visible={searchReturnVisible}
        selectedNodes={selectedNodes}
        openModal={showSearchReturn}
        edgeTypeNameList={spaceEdgeList}
        historyData={historyData} />
      {/* 查找文本 */}
      <SearchTextPanel
        mergeData={mergeData}
        visible={searchTextVisible}
        edgeTypeNameList={spaceEdgeList}
        spaceTagList={spaceTagList}
        historyData={historyData}
      />

      {/* 图谱统计 */}
      <GraphStatistics
        visible={graphStatisticsVisible}
        openModal={showGraphStatistics}
        edgeTypeNameList={spaceEdgeList}
        spaceTagList={spaceTagList}
        historyData={historyData}
        mergeData={mergeData} />
      {/* 关系净流向 */}
      <RelationTrend
        visible={relationTrendVisible}
        edgeTypeNameList={spaceEdgeList}
        historyData={historyData}
        showRelationTrend={showRelationTrend}
        mergeData={mergeData}
        setLayoutType={setLayoutType} />
      {/* 数据范围 */}
      <DataRange
        visible={dataRangeVisible}
        showDataRange={showDataRange} />
      {/* 同类节点关系 */}
      <SameTypeReltion
        visible={sameTypeRelationVisbile}
        mergeData={mergeData}
        historyData={historyData}
        showSameTypeRelation={showSameTypeRelation} />
      {
        analysisRecordLoading&&<div className='analysisRecordLoading'>
          <Spin
            tip="加载中，请稍后..."
            size="large"
            style={{
              position:"absolute",
              top:"50%",
            }}
          >
          </Spin>
        </div>
      }
      {/* 隐藏对端清单 */}
      {hideNodesVisible&&
        <HideNodesList
        visible={hideNodesVisible}
        openModal={showHideNodes}
        hideNodeId={hideNodeId}
        hideMap={hideMap}
        data={data}
        showNewEdges={showNewEdges}
        />
      }


    </>
  )
}

export default ChartExplore
