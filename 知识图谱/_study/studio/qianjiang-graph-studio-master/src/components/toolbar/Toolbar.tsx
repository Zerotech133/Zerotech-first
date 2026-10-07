import {
  ClearOutlined,
  CompressOutlined,
  FullscreenOutlined,
  LayoutOutlined,
  OneToOneOutlined,
  RadiusSettingOutlined,
  RedoOutlined,
  SearchOutlined,
  UndoOutlined,
  ZoomInOutlined,
  ZoomOutOutlined,
  PlusSquareOutlined
  // DotChartOutlined
} from '@ant-design/icons'
import {IAbstractGraph as IGraph} from '@antv/g6-core'
import {GraphinContext, GraphinData} from '@antv/graphin'
import {ToolBarItemType} from '@antv/graphin-components/lib/Toolbar'
import {
  Form,
  Input,
  message,
  Modal,
  Popover,
  Progress,
  Radio,
  RadioChangeEvent,
  Select,
  TreeSelect,
  Button
} from 'antd'
import React, {useCallback, useEffect, useRef, useState} from 'react'
import useFullscreen from './useFullscreen'
import styles from './Toolbar.module.scss'
import {graphLayoutConfig} from './LayoutConfig'
import {EGraphLayoutType} from '../../common'
import useHistory from "../../hooks/useHistory"
import API from '../../utils/api'
import SvgIcon from '@/components/svg-icon/SvgIcon';
import {cloneDeep} from 'lodash'
import saveIcon from '../../assets/svg/save.svg'
import deleteNode from '../../assets/svg/delete-node.svg'
import updateStyle from '../../assets/svg/update-style.svg'
import saveCopyIcon from '../../assets/svg/save-copy.svg'
import ModifyCombo from '../../assets/svg/modify-combo.svg'
import DeleteCombo from '../../assets/svg/delete-combo.svg'
import AddCombo from '../../assets/svg/add-combo.svg'
import RemoveCombo from '../../assets/svg/remove-combo.svg'
import ComboOperation from '../../assets/svg/combo-operation.svg'
import { addLibraryApi } from '@/api/chart'
import { handleTree, getNowTime, showSuspectLabel, recoverHideMap, recoverData } from '@/utils'
import mouseMethod from '@/utils/mouseMethod'
import { storeHideMapList } from '@/utils'

type props = {
  layoutType: EGraphLayoutType,
  setLayoutType: (type: EGraphLayoutType) => void,
  onLayoutChange: (type: EGraphLayoutType, rankdir?: string,) => void
  onUpdateStyle: (element:any, type: 'node' | 'edge') => void,
  container: HTMLElement | null,
  onSearchToggle: (flag: boolean) => void,
  onSearchTextToggle: (flag: boolean) => void,
  onClearData: () => void,
  onSearchPathToggle: (flag: boolean) => boolean,
  data: GraphinData,
  activeNodesId:any,
  activeEdgesId:any,
  saveGraphNum: number,
  setSaveGraphNum: any,
  setData: (data: GraphinData) => void,
  setBindGraphData: (data: GraphinData) => void,
  openExplore: (type: string, fn: string) => void
  openAnalysis: () => void,
  showComplicatedSearch: (flag:boolean) => void,
  showSearchReturn: (flag:boolean) => void,
  showGraphStatistics: (flag:boolean) => void,
  showRelationTrend: (flag:boolean) => void,
  showSearchReturnWeb: (flag:boolean) => void,
  openModifyCombo: () => void
  openDeleteCombo:()=>void
  openAddCombo:()=>void
  openRemoveCombo:()=>void
  openComboOperation:()=>void
  getClickNodeId:(nodeId:any)=>void,
  showDataRange: (flag:boolean) => void,
  showSameTypeRelation: (flag:boolean) => void,
  hideMap: any,
  setHideMap: (params:any) => void
}

export const Toolbar: React.FC<props> = ({
      layoutType,
      setLayoutType,
      onLayoutChange,
      container,
      onClearData,
      onSearchToggle,
      onUpdateStyle,
      onSearchTextToggle,
      onSearchPathToggle,
      data,
      activeNodesId,
      activeEdgesId,
      saveGraphNum,
      setSaveGraphNum,
      setData,
      setBindGraphData,
      openExplore,
      openAnalysis,
      showComplicatedSearch,
      showSearchReturn,
      showGraphStatistics,
      showRelationTrend,
      showSearchReturnWeb,
      openModifyCombo,
      openDeleteCombo,
      openAddCombo,
      openRemoveCombo,
      openComboOperation,
      getClickNodeId,
      showDataRange,
      showSameTypeRelation,
      hideMap,
      setHideMap
    }) => {
  const {apis, graph} = React.useContext(GraphinContext)
  const {handleZoomIn, handleZoomOut, handleAutoZoom, handleRealZoom} = apis
  const [fullscreen, toggleFullscreen] = useFullscreen(container)
  const [search, setSearch] = useState(false)
  const [searchText, setSearchText] = useState(false)
  const [searchPath, setSearchPath] = useState(false)
  const [saveVisible, setSaveVisible] = useState(false)
  const [libraryOptions, setLibraryOptions] = useState<any[]>([])
  const formRef = useRef<any>(null)
  const [value, setValue] = useState<any[]>([])
  const [remark, setRemark] = useState('')
  const [name, setName] = useState('')
  const [loading, setLoading] = useState(false)
  const [lastSaveId, setLastSaveId] = useState('')
  const [grouptreedata, setGrouptreedata] = useState<any>([])
  const graphin = React.useContext(GraphinContext)

  const {
    undo,
    redo,
    historyInfo
  } = useHistory(graph as unknown as IGraph)

  const toggleSearch = () => {
      setSearch(!search)
      onSearchToggle(!search)
      setSearchText(false)
      onSearchTextToggle(false)
  }
  const toggleSearchText = () => {
      setSearchText(!searchText)
      onSearchTextToggle(!searchText)
      setSearch(false)
      onSearchToggle(false)
  }

  const togglePathSearch = () => {
    setSearchPath(!searchPath)
    const result = onSearchPathToggle(!searchPath)
    if (!result) {
      setSearchPath(false)
    }
  }

  const handleLayoutChange = (e: RadioChangeEvent) => {
    const nextLayout: any = e.target.value
    let rankdir = ''
    if (e.target.value === '流程图布局') rankdir = 'LR'
    if (e.target.value === '层次布局') rankdir = 'TB'
    onLayoutChange(nextLayout, rankdir)
    // handleAutoZoom();
  }

  const clearData = () => {
    onClearData()
  }

  const saveAs = () => {
    setSaveVisible(true)
    API.getGroupList({type:'RG'}).then((res:any) => {
      let data = res.data
      data.push({
        id: '0',
        name: '关联图谱',
        parentId: '过程库',
        groupId: '过程库',
        type: 'RG'
      })
      data.forEach((item:any) => {
        item.groupId = item.parentId
        item.value = item.id
        item.title = item.name
        item.rootId = '过程库'
      })

      let treeData = handleTree(
        data,
        'id',
        'parentId',
        'children',
        '过程库',
        '0'
      )

      setGrouptreedata(treeData)
    })
  }

  const getData = useCallback(() => {
    // 保存的数据
    const nodeMap = new Map()
    const edgeMap = new Map()
    const cloneData = {
      ...data,
      hideMap
    }
    const savedData: any = cloneDeep(cloneData)
    savedData.nodes.forEach((it: any) => {
      nodeMap.set(it.id, it)
    })
    savedData.edges.forEach((it: any) => {
      edgeMap.set(`${it.source}-${it.target}`, it)
    })
    graph.getNodes().forEach(node => {
      const id = node.getID()
      const model = node.getModel()
      const mapNode = nodeMap.get(id)
      // 更新面板操作过的node样式保存传参
      if (activeNodesId.includes(id)) {
        let activeNode = graph.findById(id)
        // 用node更新后的样式做参数
        mapNode.style.keyshape = activeNode.getModel().style?.keyshape
      }
      if (nodeMap.has(id)) {
        mapNode.custom = {
          coordinates: node.getBBox(),
          states: node.getStates()
        }
      }
      // 点x,y保存
      savedData.nodes.forEach(ele => {
        if (model.id === ele.id) {
          ele.oldX = model.x
          ele.oldY = model.x
        }
      })
    })
    // 更新面板操作过的edge样式保存传参
    let activeEdgesNew = activeEdgesId.map((id:any) => {
      let model = graph.findById(id).getModel()
      return {
        id: `${model.source}-${model.target}`,
        keyshape: model.style?.keyshape
      }
    })

    graph.getEdges().forEach(edge => {
      const id = `${edge.getSource().getID()}-${edge.getTarget().getID()}`
      let mapEdge = edgeMap.get(id)
      let activeEdge = activeEdgesNew.find((item:any) => {return item.id === id})
      // 用edge更新后的样式做参数
      if (activeEdge) {
        mapEdge.style.keyshape = activeEdge.keyshape
      }
      if (edgeMap.has(id)) {
        mapEdge.custom = {
          states: edge.getStates()
        }
      }
    })
    // 图类型保存
    savedData.layoutType = layoutType


    return JSON.stringify(savedData)
  }, [data, graph, activeNodesId, activeEdgesId, layoutType,hideMap])
  // 保存事件
  const save = async () => {
    if (!lastSaveId) {
      message.warn('请先另存')
      return
    }
    if (graph.getNodes().length === 0) {
      message.warn('当前视图暂无数据')
      return
    }
    setLoading(true)
    try {
      graph.toFullDataURL(async (res: string) => {
        const imageRes: any = await API.saveGraphImage(res)
        const imageUrl = imageRes.data[0].filePath
        const params = {
          id: lastSaveId,
          data: getData(),
          imageUrl
        }
        await API.updateLibrary(params);
        message.success('保存成功')
        setSaveGraphNum(saveGraphNum + 1)
      }, 'image/png',{backgroundColor: '#fff',padding: 10})

    } catch (e) {
      message.error('接口请求错误')
      console.error(e)
    }
    setLoading(false)
  }
  // 删除节点
  const handleRemoveNode=()=>{
    mouseMethod.handelRemoveNode(graph,setHideMap,setData, setBindGraphData,hideMap,data)
  }
   // 更新样式
   const handleUpdateStyle=()=>{
    let nodes=graph.findAllByState('node', 'selected')
    let edges=graph.findAllByState('edge', 'selected')
    if (nodes.length) {
      onUpdateStyle(nodes[nodes.length-1],'node')
      return
    }
    if (edges.length) {
      onUpdateStyle(edges[edges.length-1],'edge')
      return
    }
    message.warn('请先选择需要更新样式的节点或边')
  }
  const recover = useCallback((savedData: any) => {
    const map = new Map()
    savedData.nodes.forEach(it => {
      map.set(it.id, it.custom)
      Reflect.deleteProperty(it, 'custom')
    })
    savedData.edges.forEach(it => {
      map.set(`${it.source}-${it.target}`, it.custom)
      Reflect.deleteProperty(it, 'custom')
    })
    const newData = {
      nodes:savedData.nodes,
      edges:savedData.edges
    }
    setData(newData)
    setBindGraphData(newData)
    if (savedData.hideMap) {
      setHideMap(savedData.hideMap)
      storeHideMapList(savedData.hideMap, newData)
    } else {
      storeHideMapList({}, newData)
    }
    graph.render()

    setTimeout(() => {
      graph.getNodes().forEach((node: any) => {
        const id = node.getID()
        if (map.has(id)) {
          const custom = map.get(id)
          node.updatePosition(custom.coordinates)
          if (custom?.states) {
            custom.states.forEach((it: any) => node.setState(it, true))
          }
        }
      })
      graph.getEdges().forEach(edge => {
        const id = `${edge.getSource().getID()}-${edge.getTarget().getID()}`
        edge.refresh()
        if (map.has(id)) {
          const custom = map.get(id)
          if (custom?.states) {
            custom.states.forEach((it: any) => edge.setState(it, true))
          }
        }
      })
      graph.refresh()
      graph.fitCenter()
    })
    // setTimeout(() => {
    //   graph.getNodes().forEach((node: any) => {
    //     const model = node.getModel()
    //     // 恢复图保存前形状
    //     graph.updateItem(node, {
    //       x: model.oldX,
    //       y: model.oldY,
    //     });
    //   })
    // },1000)

  }, [graph,setHideMap, setData,setBindGraphData])

  useEffect(() => {
    const params: any = {}
    const paramList = window.location.search.slice(1).split('&')
    paramList.forEach(it => {
      const [key, value] = it.split('=')
      params[key] = value
    })
    if (!params.id) {
      return
    } else{
      setLastSaveId(params.id)
    }
    API.getGraph(params.id).then((res: any) => {
      if (res.data?.data) {
        const savedData = JSON.parse(res.data.data)
        // 设置默认图类型
        setLayoutType(savedData.layoutType)
        recover(savedData)
      }
    }, (e) => {
      message.error('接口请求错误')
      console.error(e)
    }).catch((e) => {
      console.error(e)
    })
  }, [recover, setLayoutType])

  const handleCancel = useCallback(() => {
    setLibraryOptions([])
    setSaveVisible(false)
  }, [])

  const handleSubmit = useCallback(async () => {
    if (!saveVisible) {
      return
    }
    if (graph.getNodes().length === 0) {
      message.warn('当前视图暂无数据')
      return
    }
    try {
      await formRef.current!.validateFields()
    } catch (e) {
    }
    setLoading(true)
    try {
      graph.toFullDataURL(async (res: string) => {
        const imageRes: any = await API.saveGraphImage(res)
        const imageUrl = imageRes.data[0].filePath
        const option = libraryOptions.find(it => it.id === value[0])
        let params
        const jsonData = getData()
        if (option) {
          params = {
            imageUrl,
            remark,
            name,
            data: jsonData,
            libraryId: option.id
          }
        } else {
          params = {
            imageUrl,
            remark,
            name,
            data: jsonData,
            libraryName: value[0]
          }
        }
        addLibraryApi(params).then((res) => {
          setLastSaveId(res.data.id)
          setLoading(false)
          setSaveVisible(false)
          message.success('另存成功')
          setSaveGraphNum(saveGraphNum + 1)
        }).catch((err) => {
          setLoading(false);
          message.error('另存失败，'+ err.msg);
        })

      }, 'image/png',
          {backgroundColor: '#fff',padding: 10});
    } catch (e) {
      message.error('接口请求错误')
      console.error(e)
      setLoading(false)
    }
  }, [graph, libraryOptions, value, saveVisible, remark, name, getData, setSaveGraphNum, saveGraphNum])

  const handleChange = useCallback((value) => {
    const current = value.length ? value.slice(-1) : []
    setValue(current)
    formRef.current!.setFields([{
      name: 'library',
      value: current
    }])
  }, [])

  const handleRemarkChange = useCallback((e) => {
    setRemark(e.target.value)
  }, [setRemark])

  const handleNameChange = useCallback((e) => {
    setName(e.target.value)
  }, [setName])

  // 新增画布
  const addCanvas = () => {
    // const url = window.location.href
    const urlParams = new URLSearchParams(window.location.search);
    const caseId = urlParams.get("caseId");
    const port = urlParams.get("port")
    // const str = url.split('&')
    // .filter((item:string) => item.indexOf('id') !== 0).join('&')
    const newUrl = `${window.location.origin}${window.location.pathname}?caseId=${caseId}&port=${port}`
    window.open(newUrl, '_blank')
  }
  // 打开复杂查询弹框
  const openComplicatedSearch = () => {
    showComplicatedSearch(true)
  }
  // 打开查找回路弹框
  const openSearchReturn = () => {
    showSearchReturn(true)
  }
  // 打开图谱统计弹框
  const openGraphStatistics = () => {
    showGraphStatistics(true)
  }

  const options: ToolBarItemType[] = [
    {
      key: 'layout',
      name: <LayoutOutlined/>,
      description: '切换布局',
      hasSeparator: false, // 是否有分隔符
      showType: '通\n用', // 是否显示标签类型
      renderTooltip() {
        return (
          <Radio.Group
            value={ layoutType }
            className={styles.layoutSetting}
            onChange={handleLayoutChange}
          >
            {graphLayoutConfig.map(({type, title, icon: Icon}) => (
              <Radio value={title} key={title}>
                {<Icon/>} {title}
              </Radio>
            ))}
          </Radio.Group>
        )
      }
    },
    {
      key: 'plusSquareOutlined',
      name: <PlusSquareOutlined/>,
      hasSeparator: false,
      description: '新增\n画布',
      action: () => {
        addCanvas()
      },
    },
    {
      key: 'undo',
      cname: `撤销操作,进度:${historyInfo.currentStep} / ${historyInfo.allStep}`,
      name: <UndoOutlined/>,
      hasSeparator: false,
      description: '撤销\n操作',
      action: () => {
        let currentData = undo()
        recoverHideMap(graph.getNodes(), graph.getEdges(), setHideMap)
        const recoverGraphData:GraphinData = recoverData(currentData, data, graph)
        if (recoverGraphData) {
          setData(recoverGraphData)
        }
        // 添加嫌疑人标签
        showSuspectLabel(graph)
      },
      renderTooltip: () => {
        const {currentStep, allStep} = historyInfo
        const percent = Math.round((currentStep / allStep) * 100)
        return (
          <div>
            <Progress percent={percent} showInfo={false}/>
          </div>
        )
      },
    },
    {
      key: 'redo',
      cname: `重做操作,进度:${historyInfo.currentStep} / ${historyInfo.allStep}`,
      name: <RedoOutlined/>,
      hasSeparator: false, // 是否有分隔符
      description: '重做\n操作',
      action: () => {
        let currentData = redo()
        // 图数据用栈里的
        // store.dispatch(setCurrentData({'currentData': currentData}))
        recoverHideMap(graph.getNodes(), graph.getEdges(), setHideMap)
        if (historyInfo.currentStep !== historyInfo.allStep) {
          const recoverGraphData:GraphinData = recoverData(currentData, data, graph)
          if (recoverGraphData) {
            setData(recoverGraphData)
          }
        }
        // 添加嫌疑人标签
        showSuspectLabel(graph)
      },
      renderTooltip: () => {
        const {currentStep, allStep} = historyInfo
        const percent = Math.round((currentStep / allStep) * 100)
        return (
          <div>
            <Progress percent={percent} showInfo={false}/>
          </div>
        )
      },
    },
    {
      key: 'fullscreen',
      name: fullscreen ? <FullscreenOutlined/> : <FullscreenOutlined/>,
      hasSeparator: false,
      description: fullscreen ? '还原' : '全屏',
      action: toggleFullscreen,
    },
    {
      key: 'zoomOut',
      name: <ZoomInOutlined/>,
      hasSeparator: false,
      description: '放大',
      action: () => {
        handleZoomOut()
      },
    },
    {
      key: 'zoomIn',
      name: <ZoomOutOutlined/>,
      description: '缩小',
      hasSeparator: false,
      action: () => {
        handleZoomIn()
      },
    },
    {
      key: 'compress',
      name: <CompressOutlined/>,
      description: '适应\n画布',
      hasSeparator: false, // 是否有分隔符
      action: () => {
        handleAutoZoom()
      },
    },
    {
      key: 'one-to-one',
      name: <OneToOneOutlined/>,
      description: '实际\n尺寸',
      hasSeparator: false,
      action: () => {
        handleRealZoom()
      },
    },
    {
      key: 'update-style',
      name: <img src={updateStyle} alt="updateStyle" />,
      description: '更新\n样式',
      hasSeparator: false,
      action: () => {
        handleUpdateStyle()
      },
    },
    {
      key: 'removeNode',
      name: <img src={deleteNode} alt="removeNode" />,
      description: '删除\n节点',
      hasSeparator: false,
      action: () => {
        const node = graphin.contextmenu.node
        const edge = graphin.contextmenu.edge
        edge.handleClose()
        node.handleClose()
        handleRemoveNode()
      },
    },
    {
      key: 'clean',
      name: <ClearOutlined/>,
      description: '清空',
      hasSeparator: false, // 是否有分隔符
      action: () => {
        clearData()
      }
    },
    {
      key: 'saveAsLocal',
      name: <SvgIcon iconClass='save-as-local' />,
      description: '保存图片',
      hasSeparator: true, // 是否有分隔符
      action: () => {
        const name = '奇数百炼-' + getNowTime()
        graph.downloadFullImage(name, 'image/jpeg', {
          backgroundColor: '#fff',
          padding: [30, 15, 15, 15],
        });
      }
    },
    {
      key: 'findShortestPath',
      name: <RadiusSettingOutlined/>,
      description: searchPath ? '取消\n查询' : '查询最\n短路径',
      wideWidth: true,
      hasSeparator: false,
      showType: '前\n端', // 是否显示标签类型
      action: togglePathSearch,
    },
    {
      key: 'pageRank',
      name: <SvgIcon iconClass='page-rank' />,
      description: '核心度\n详情',
      wideWidth: true,
      hasSeparator: false,
      action: () => {
        openAnalysis()
      }
    },
    {
      key: 'findAllPath',
      name: <SvgIcon iconClass='explore-web' />,
      description: '图上查\n找路径',
      wideWidth: true,
      hasSeparator: false,
      action: () => {
        const selectedNodes = graph.findAllByState('node', 'selected')
        let selectedId:any = selectedNodes.map((item:any) => item.getModel().id)
        let set:any = new Set(selectedId)
        selectedId = [...set]
        if (selectedId.length !== 2) {
          message.warn('请选择有且两个节点(使用Ctrl按键)')
          return
        }
        openExplore('exploreWeb','path')
      }
    },
    {
      key: 'searchReturnWeb',
      name: <SvgIcon iconClass='search-return-web' />,
      description: '图上查\n找回路',
      wideWidth: true,
      hasSeparator: false,
      action: () => {
        showSearchReturnWeb(true)
      },
    },
    {
      key: 'AddCombo',
      name: <img src={AddCombo} alt="AddCombo" />,
      description: '加入\n集合',
      hasSeparator: false,
      action: () => {
        openAddCombo()
        getClickNodeId(undefined)
      }
    },
    {
      key: 'DeleteCombo',
      name: <img src={DeleteCombo} alt="ModifyCombo" />,
      description: '删除\n集合',
      hasSeparator: false,
      action: () => {
        openDeleteCombo()
      }
    },
    {
      key: 'ModifyCombo',
      name: <img src={ModifyCombo} alt="ModifyCombo" />,
      description: '修改\n集合',
      hasSeparator: false,
      action: () => {
        openModifyCombo()
      }
    },
    {
      key: 'RemoveCombo',
      name: <img src={RemoveCombo} alt="RemoveCombo" />,
      description: '移出\n集合',
      hasSeparator: false,
      action: () => {
        openRemoveCombo()
        getClickNodeId(undefined)
      }
    },
    {
      key: 'ComboOperation',
      name: <img src={ComboOperation} alt="ComboOperation" />,
      description: '集合\n操作',
      hasSeparator: true,
      action: () => {
        openComboOperation()
      }
    },
    {
      key: 'saveAs',
      name: <img src={saveCopyIcon} alt="saveCopyIcon" />,
      description: '另存为',
      showType: '后\n端', // 是否显示标签类型
      hasSeparator: false,
      wideWidth: true,
      action: () => {
        saveAs()
      },
    },
    {
      key: 'save',
      name: <img src={saveIcon} alt="saveIcon" />,
      description: '保存',
      hasSeparator: false,
      action: () => {
        save()
      },
    },
    {
      key: 'search',
      name: <SearchOutlined/>,
      description: search ? '取消\n查询' : '查询',
      action: toggleSearch,
      hasSeparator: false
    },
    {
      key: 'complicatedSearch',
      name: <SvgIcon iconClass='complicated-search' />,
      description: '复杂\n查询',
      hasSeparator: false,
      action: () => {
        openComplicatedSearch()
      },
    },
    {
      key: 'searchText',
      name: <SvgIcon iconClass='search-text' />,
      description:searchText ? '取消\n查找' :'查找\n文本',
      hasSeparator: false,
      action: toggleSearchText,
    },
    {
      key: 'statistics',
      name: <SvgIcon iconClass='statistics' />,
      description: '统计\n查询',
      hasSeparator: false,
      action: () => {
        openGraphStatistics()
      },
    },
    {
      key: 'searchReturn',
      name: <SvgIcon iconClass='search-return' />,
      description: '查找\n回路',
      hasSeparator: false,
      action: () => {
        openSearchReturn()
      },
    },
    {
      key: 'findPath',
      name: <SvgIcon iconClass='findPath' />,
      description: '查找路径',
      hasSeparator: false,
      action: () => {
        openExplore('explore','path')
      },
    },
    {
      key: 'connectivityAnalysis',
      name: <SvgIcon iconClass='connectivity-analysis' />,
      description: '连通性\n分析',
      hasSeparator: false,
      wideWidth: true,
      action: () => {
        openExplore('explore','graph')
      },
    },
    {
      key: 'relationTrend',
      name: <SvgIcon iconClass='relation-trend' />,
      description: '关系净\n流向',
      wideWidth: true,
      hasSeparator: false,
      action: () => {
        showRelationTrend(true)
      }
    },
    {
      key: 'sameTypeRelation',
      name: <SvgIcon iconClass='same-type-relation' />,
      description: '同类节\n点关系',
      wideWidth: true,
      hasSeparator: false,
      action: () => {
        showSameTypeRelation(true)
      }
    },
    {
      key: 'dataRange',
      name: <SvgIcon iconClass='data-range' />,
      description: '数据\n范围',
      wideWidth: true,
      hasSeparator: false,
      action: () => {
        showDataRange(true)
      }
    },
    // {
    //   key: 'timeline',
    //   name: <DotChartOutlined/>,
    //   description: '时光轴',
    //   action: () => {
    //     window.open('#/timeline', '_blank')
    //   },
    // },
  ]

  const groupChange = (groupId:string)=> {
    let params = {
      groupId: groupId === '关联图谱' ? '0' : groupId,
      type: 'RG'
    }
    API.getLibraryList(params).then((res: any) => {
      setLibraryOptions(res.data)
    }).catch((e) => {
      message.error('接口请求错误')
      console.error(e)
    })
  }
  // 清空 “保存图” 弹窗的内容
  useEffect(() => {
    if (!saveVisible) {
      return
    }
    formRef.current!.resetFields()
  }, [saveVisible])

  return (
    <div className={styles.controlWrap}>
      <>
        {options.map((item) => {
          if (item.renderTooltip) {
            return (
              <div key={item.key} style={{display: 'flex',alignItems: 'flex-start', height: '60px'}}>
              {
                item.showType &&
                <div className={styles.type}>
                  <div className={styles.text}>{item.showType}</div>
                  <div className={styles.arrow}></div>
                </div>
              }
                <Popover
                  placement="bottom"
                  content={item.renderTooltip()}
                  title={item.cname}
                  trigger="hover"
                  key={item.key}
                >
                  <div
                  className={styles.controlItem}
                  onClick={item.action}
                  style={{width: item.wideWidth?'36px': '24px','marginRight': '10px'}}>
                    <div>{item.name}</div>
                    <div>{item.description}</div>
                  </div>
                </Popover>
              </div>
            )
          }
          return (
            <div key={item.key}  style={{display: 'flex',alignItems: 'flex-start', height: '60px'}}>
            {
              item.showType &&
              <div className={styles.type} >
                <div className={styles.text}>{item.showType}</div>
                <div className={styles.arrow}></div>
              </div>
            }
            <div
             className={styles.controlItem}
             onClick={item.action}
             style={{width: item.wideWidth?'36px': '24px','marginRight': item.hasSeparator ? '60px' : '10px'}}>
                <div>{item.name}</div>
                <div>{item.description}</div>
              </div>
            </div>
          )
        })}
                {
          <Modal
          title="保存图"
          visible={saveVisible}
          onCancel={handleCancel}
          maskClosable={false}
          okButtonProps={{loading}}
          width={600}
          footer={null}>
            <Form
              ref={formRef}
              layout="vertical"
              onFinish={handleSubmit}
            >
              <Form.Item label="分组名" name="parentId" rules={[{
               required: true, message: '请选择分组名'
              }]}>
                <TreeSelect
                allowClear
                treeData={grouptreedata}
                onSelect={groupChange}
                placeholder={'请选择分组名'}
                style={{ width: '100%' }}
                ></TreeSelect>
              </Form.Item>
              <Form.Item label="图谱库名" name="library" rules={[{required: true, message: '请选择/输入图谱库名'}]}>
                <Select
                  mode="tags"
                  style={{width: '100%'}}
                  onChange={handleChange}
                  placeholder="请选择/输入图谱库名"
                >
                  {
                    libraryOptions.map(it => (
                      <Select.Option key={it.id} value={it.id}>{it.name}</Select.Option>
                    ))
                  }
                </Select>
              </Form.Item>
              <Form.Item label="图谱名" name="name" rules={[{required: true, message: '请输入图谱名'}]}>
                <Input style={{width: '100%'}} placeholder="请输入图谱名" onChange={handleNameChange} />
              </Form.Item>
              <Form.Item label="备注" name="remark">
                <Input style={{width: '100%'}} placeholder="请输入备注" onChange={handleRemarkChange} />
              </Form.Item>
              <Form.Item wrapperCol={{ offset: 8, span: 16 }}>
                <Button  onClick={handleCancel}>取消</Button>
                <Button type="primary" htmlType="submit" style={{marginLeft:'20px'}}>提交</Button>
              </Form.Item>
            </Form>
          </Modal>
        }
      </>
    </div>
  )
}
