import React, {useCallback, useContext, useEffect, useRef, useState} from 'react'
import {
  Button,
  Col,
  Form,
  InputNumber,
  message,
  Modal,
  Row,
  Select,
  Tabs
} from 'antd'
// import { searchPathType, pathDirections as directions} from "@/utils/common-data"
import { searchPathType, pathDirections as directions} from "@/utils/common-data"

import {DebounceSelect} from "../"
import { findNodeApi } from "@/api/chart"
import {GraphinContext} from "@antv/graphin";
import {IG6GraphEvent, Algorithm} from "@antv/g6";
import { dealNodes, dealEdges } from '@/utils'
import './ExploreWeb.scss'
// import { PlusSquareOutlined, MinusSquareOutlined } from '@ant-design/icons'
import {graphStrengths} from '@/common'
import {findSubGraphApi} from '@/api/chart'
import { EdgeTypeItem } from '@/views/chart-explore/form-items/FormItems'
import store from '@/redux/store/index'
import { setSearchNodes } from '@/redux/action'
import mouseMethod from '@/utils/mouseMethod'

const {TabPane} = Tabs
const {Option} = Select
// @ts-ignore
// const { findAllPath, findShortestPath } = Algorithm
const { findShortestPath } = Algorithm

type props = {
  visible: boolean,
  data: any,
  setVisible: (visible: boolean) => void,
  onExploreClose: () => void,
  mergeData: (data: any) => void,
  activeKey: string,
  edgeTypeNameList: any,
  iconPathList: any
}

export const ExploreWeb: React.FC<props> = ({visible, data, setVisible, onExploreClose, mergeData, activeKey, edgeTypeNameList,iconPathList}) => {
  const [startVertexId, setStartVertexId] = useState('')
  const [endVertexId, setEndVertexId] = useState('')
  const [vertexlist, setVertexList] = useState<any>([])
  const {graph} = useContext(GraphinContext)
  const lastNodeIdRef = useRef<string>('')
  const [graphLoading, setGraphLoading] = useState(false)
  const [nativeActiveKey, setNativeActiveKey] = useState('path')
  const [selectorForm, setSelectorForm] = useState<any>(null)
  const [form] = Form.useForm();
  const [selectedEdgeType, setSelectedEdgeType] = useState<any>([]) // 子图选中的类型
  const [edgeParamList, setEdgeParamList] =useState<any>([])
  const graphRef = useRef<any>(null)
  const [form1] = Form.useForm();

  useEffect(() => {
    const handleClick = (evt: IG6GraphEvent) => {
      if (evt.item === null) {
        return
      }
      lastNodeIdRef.current = evt.item.getID()
    }
    graph.on('node:click', handleClick)
    return () => {
      graph.off('node:click', handleClick)
    }
  }, [graph])

  const findNode = (value: string) => {
    return new Promise(((resolve, reject) => {
      if (!value) {
        reject()
      }
      findNodeApi({keyword: value}).then((res: any) => {
        const iconPathList: any = store.getState().iconPathList
        resolve(res.data.map((it: any) => ({
          label: (
            <div  style={{display:"flex",alignItems: "center"}}>
              {iconPathList[it.tag.name]&&
              <img style={{width:"15px",height:"15px",marginRight: "5px"}} src={iconPathList[it.tag.name]} alt="" />
              }
              <span>{it.name}</span>
            </div>
          ),
          value: it.id
        })))
      }).catch(e => {
        message.error('接口请求错误')
        reject(e)
      })
    }))
  }
  const handleCancel = () => {
    onExploreClose()
  }
  // 筛选反向路径
  const backwardPath = (allPath: any) => {
    allPath = allPath.filter((item: any) => {
      let num: number = 0
      // 页面所有边target-source格式
      const totalBackwardEdges = data.edges.map((item: any) => item.target + '-' + item.source)
      item.forEach((ele: string, index: number) => {
        if (index < item.length - 1) {
          let edge = ele + '-' + item[index + 1]
          if (totalBackwardEdges.includes(edge)) {
            num ++
          }
        }
      })
      return num === item.length - 1
    })
    return allPath
  }
  // 获取2点间全路径正向  反向  无向
  // const getTotalPath = (direction: string) => {
  //   let allPath // 全路径
  //   if (direction === 'forward') {// 正向
  //     allPath = findAllPath(data, startVertexId, endVertexId, true)
  //   } else {
  //     // 反向 无向
  //     allPath = findAllPath(data, startVertexId, endVertexId)
  //     if (direction === 'backward') {// 反向
  //       // 筛选出反向路径
  //       allPath = backwardPath(allPath)
  //     }
  //   }
  //   // console.log(allPath);

  //   return allPath
  // }
  // 获取2点间最短路径正向  反向  无向
  const getShortestPath = (direction: string) => {
    let allPath
    if (direction === 'forward') {//正向
      allPath = findShortestPath(data, startVertexId, endVertexId, true).allPath
      if (allPath === undefined) return []
    } else {
      // 反向 无向
      allPath = findShortestPath(data, startVertexId, endVertexId).allPath
      if (allPath === undefined) return []
      if (direction === 'backward') {// 反向
        // 筛选出反向路径
        allPath = backwardPath(allPath)
      }
    }
    return allPath
  }

  // 图像查找全路径
  function getAllPath(maxStep:number ,direction: string, start: any, end: any, searchPathType) {
    let step1 = maxStep % 2  === 0 ? maxStep / 2 : Math.ceil(maxStep / 2)
    let step2 = maxStep - step1
    // 正向foward  反向backward 无向undirected
    const hashMap1 = new Map()
    const hashMap2 = new Map()
    // 解决爆炸点问题 最小步数
    // 过滤筛选类型,聚合点无类型
    const searchData = data.edges.filter(item => searchPathType.includes(item.edgeTypeName))
    if (direction === "undirected") {
      undirectedDFS(searchData,hashMap1,start,end,step1,[],new Set(),0,new Map())
      undirectedDFS(searchData,hashMap2,end,start,step2,[],new Set(),0,new Map())
    }
    else if (direction === "forward") {
      // 从起点开始查
      forwardDFS(searchData,hashMap1,start,end,step1,[],0,new Map())
      // 从终点开始查
      backwardDFS(searchData,hashMap2,start,end,step2,[],0,new Map())
    }
    else if (direction === "backward") {
      // 从终点开始查
      forwardDFS(searchData,hashMap1,start,end,step1,[],0,new Map())
      // 从起点开始查
      backwardDFS(searchData,hashMap2,end,start,step2,[],0,new Map())
    }
    // 搜寻共同点
    const tmpList = []
    Array.from(hashMap1.keys()).forEach(item => {
      if (Array.from(hashMap2.keys()).includes(item))  tmpList.push(item)
    })
    let edgeList = []
    tmpList.forEach(item => {
      edgeList.push(hashMap1.get(item))
      edgeList.push(hashMap2.get(item))
    })
    edgeList.push(hashMap1.get(start))
    edgeList.push(hashMap1.get(end))
    edgeList.push(hashMap2.get(start))
    edgeList.push(hashMap2.get(end))
    const flatten = (arr) => {
      return arr.reduce(function (flat, toFlatten) {
        return flat.concat(Array.isArray(toFlatten) ? flatten(toFlatten) : toFlatten);
      }, []);
    }
    edgeList = edgeList.filter(item => item)
    const set:any = new Set(flatten(edgeList))
    edgeList = [...set]
    return edgeList
  }


  // function forwardDFS(data,hashMap, start, end, step, path) { // 正向
  //   if (step === 0 || start === end) {
  //     // 当步数为0 || 到达终点时结束递归
  //     return
  //   }
  //   data.forEach(edge => {
  //     const { source, target, id } = edge
  //     if (source === start) {
  //       const newPath = path.concat(id)
  //       if (!hashMap.has(target)) {
  //         hashMap.set(target, [newPath])
  //       } else {
  //         hashMap.get(target).push(newPath)
  //       }
  //       forwardDFS(data,hashMap, target, end, step - 1, newPath)
  //     }
  //   })
  // }
  function forwardDFS(data, hashMap, start, end, step, path, depth, visitedMap) {
    if (step === 0 || start === end) {
      return
    }
    data.forEach(edge => {
      const { source, target, id } = edge;
      if (source === start) {
        const newPath = path.concat(id);
        if (!hashMap.has(target)) {
          hashMap.set(target, [newPath]);
        } else {
          const paths = hashMap.get(target);
          let isVisited = false;
          for (let i = 0; i < paths.length; i++) {
            const visitedPath = paths[i];
            if (visitedPath.length >= newPath.length && visitedPath.slice(0, newPath.length).join('') === newPath.join('')) {
              isVisited = true;
              break;
            }
          }
          if (!isVisited) {
            paths.push(newPath);
          }
        }
        if (!visitedMap.has(target) || visitedMap.get(target) < depth) {
          visitedMap.set(target, depth);
          forwardDFS(data, hashMap, target, end, step - 1, newPath, depth + 1, visitedMap);
        }
      }
    });
  }

  // function backwardDFS(data,hashMap,start,end,step, path) { // 反向
  //   if (step === 0 || end === start) {
  //     return
  //   }
  //   data.forEach(edge => {
  //     const { source, target, id } = edge
  //     if (target === end) {
  //       const newPath = path.concat(id)
  //       if (!hashMap.has(source)) {
  //         hashMap.set(source, [newPath])
  //       } else {

  //         hashMap.get(source).push(newPath)
  //       }
  //       backwardDFS(data,hashMap, start, source, step - 1, newPath)
  //     }
  //   })
  // }

  function backwardDFS(data,hashMap,start,end,step, path, depth, visitedMap) { // 反向
    if (step === 0 || end === start) {
      return
    }
    data.forEach(edge => {
      const { source, target, id } = edge
      if (target === end) {
        const newPath = path.concat(id)
        if (!hashMap.has(source)) {
          hashMap.set(source, [newPath])
        } else {
          const paths = hashMap.get(source);
          let isVisited = false;
          for (let i = 0; i < paths.length; i++) {
            const visitedPath = paths[i];
            if (visitedPath.length >= newPath.length && visitedPath.slice(0, newPath.length).join('') === newPath.join('')) {
              isVisited = true;
              break;
            }
          }
          if (!isVisited) {
            paths.push(newPath);
          }
        }
        if (!visitedMap.has(source) || visitedMap.get(source) < depth) {
          visitedMap.set(source, depth);
          backwardDFS(data, hashMap, start, source, step - 1, newPath, depth + 1, visitedMap);
        }
      }
    })
  }

  function undirectedDFS(data, hashMap, start, end, step, path, visited, depth, visitedMap) {
    if (step === 0 || start === end) {
      // 当步数为0
      return;
    }
    visited.add(start); // 将当前节点标记为已访问
    data.forEach((edge) => {
      const { source, target, id } = edge;
      if (source === start || target === start) {
        // 当前节点为该边的起点或终点
        const nextNode = start === source ? target : source; // 计算下一个节点
        if (!visited.has(nextNode)) {
          // 避免重复访问同一个节点
          const newPath = path.concat(id);
          if (!hashMap.has(nextNode)) {
            hashMap.set(nextNode, [newPath]);
          } else {
            const paths = hashMap.get(nextNode);
            let isVisited = false;
            for (let i = 0; i < paths.length; i++) {
              const visitedPath = paths[i];
              if (visitedPath.length >= newPath.length && visitedPath.slice(0, newPath.length).join('') === newPath.join('')) {
                isVisited = true;
                break;
              }
            }
            if (!isVisited) {
              paths.push(newPath);
            }
          }
          if (!visitedMap.has(nextNode) || visitedMap.get(nextNode) < depth) {
            visitedMap.set(nextNode, depth);
            undirectedDFS(data, hashMap, nextNode, end, step - 1, newPath, visited,depth, visitedMap);
          }
        }
      }
    });
    visited.delete(start); // 回溯时取消标记
  }

  // function undirectedDFS(data, hashMap, start, end, step, path, visited) {
  //   if (step === 0 || start === end) {
  //     // 当步数为0
  //     return;
  //   }

  //   visited.add(start); // 将当前节点标记为已访问
  //   data.forEach((edge) => {
  //     const { source, target, id } = edge;
  //     if (source === start || target === start) {
  //       // 当前节点为该边的起点或终点
  //       const nextNode = start === source ? target : source; // 计算下一个节点
  //       if (!visited.has(nextNode)) {
  //         // 避免重复访问同一个节点
  //         const newPath = path.concat(id);
  //         if (!hashMap.has(nextNode)) {
  //           hashMap.set(nextNode, [newPath]);
  //         } else {
  //           hashMap.get(nextNode).push(newPath);
  //         }
  //         undirectedDFS(data, hashMap, nextNode, end, step - 1, newPath, visited);
  //       }
  //     }
  //   });
  //   visited.delete(start); // 回溯时取消标记
  // }
  // 图上查找路径
  function onPathFinish(values: any) {
    const {direction, maxStep, edgeTypeNameList, searchPathType} = values
    let res: any
    if (searchPathType === 'all') {
      res = getAllPath(maxStep,direction,startVertexId,endVertexId,edgeTypeNameList)
      if (res.length === 0) {
        message.warning('未查询到数据')
      } else {
        setVisible(false)
        store.dispatch(setSearchNodes({searchNodes: [startVertexId, endVertexId]}))

        const graphData = mouseMethod.getNewNodesEdges(data, res)
        mergeData({...graphData, highlight: true})
      }
    } else {
      res = getShortestPath(direction)
      if (res.length === 0) {
        message.warning('未查询到数据')
      } else {
        setVisible(false)
        store.dispatch(setSearchNodes({searchNodes: [startVertexId, endVertexId]}))
        let edges = []
        let nodesId = []
        data.edges.forEach(edge => {
          res.forEach(it => {
            // 高亮点
            it.forEach((node,index) => {
              if (direction === 'forward') {
                if (edge.source === node && edge.target === it[index+1]) {
                  edges.push(edge)
                  nodesId.push(edge.source)
                  nodesId.push(edge.target)
                }
              }
              else if (direction === 'backward') {
                if (edge.target === node && edge.source === it[index+1]) {
                  edges.push(edge)
                  nodesId.push(edge.source)
                  nodesId.push(edge.target)
                }
              }
              else {
                if ((edge.target === node && edge.source === it[index+1]) || (edge.source === node && edge.target === it[index+1])) {
                  edges.push(edge)
                  nodesId.push(edge.source)
                  nodesId.push(edge.target)
                }
              }
            })
          })
        })
        let set: any = new Set(nodesId)
        nodesId = [...set]
        const nodes = data.nodes.filter(node => nodesId.includes(node.id))
        mergeData({nodes, edges, highlight: true})
      }
    }

    // let allPath: any
    // if (searchPathType === 'all') {
    //   // 获取2点间全路径正向  反向  无向
    //   allPath = getTotalPath(direction)
    // } else {
    //   allPath = getShortestPath(direction)
    // }
    // if (allPath.length === 0) {
    //   message.warning('未查询到数据')
    //   return
    // }
    // // 根据最大步数筛选路径
    // allPath = allPath.filter((item: any) => item.length < maxStep + 2)
    // let allEdges: any = []
    // // 路径数据source-target格式
    // allPath.forEach((item: any) => {
    //   item.forEach((ele: any, index: number) => {
    //     if (index < item.length - 1) {
    //       const edges = ele + '-' + item[index + 1]
    //       allEdges.push(edges)
    //     }
    //   })
    // })
    // 匹配路径点集合
    // let nodesId: any = []
    // 路径匹配边类型
    // graph.getEdges().forEach(it => {
    //   graph.clearItemStates(it)
    //   const model: any = it.getModel()
    //   const {source, target, edgeTypeName} = model
    //   const targetSource = target + '-' + source
    //   const sourceTarget = source + '-' + target
    //   if (direction === 'forward') {// 正向
    //     if (allEdges.includes(sourceTarget) && (edgeTypeNameList.includes(edgeTypeName) || edgeTypeNameList.includes('all'))) {
    //       graph.setItemState(it, 'actived', true)
    //       nodesId = [...nodesId, source, target]
    //     } else {
    //       graph.setItemState(it, 'disabled', true)
    //     }
    //   } else if (direction === 'backward') {// 反向
    //     if (allEdges.includes(targetSource) && (edgeTypeNameList.includes(edgeTypeName) || edgeTypeNameList.includes('all'))) {
    //       graph.setItemState(it, 'actived', true)
    //       nodesId = [...nodesId, source, target]
    //     } else {
    //       graph.setItemState(it, 'disabled', true)
    //     }
    //   } else {// 无向
    //     if ((allEdges.includes(sourceTarget) || allEdges.includes(targetSource))
    //         &&(edgeTypeNameList.includes(edgeTypeName) || edgeTypeNameList.includes('all'))
    //         ) {
    //       graph.setItemState(it, 'actived', true)
    //       nodesId = [...nodesId, source, target]
    //     } else {
    //       graph.setItemState(it, 'disabled', true)
    //     }
    //   }
    // })
    // if (nodesId.length === 0) {
    //   message.warning('未查询到数据')
    // } else {
    //   setVisible(false)
    // }
    // const set: any = new Set(nodesId)
    // nodesId = [...set]
    // // 点高亮
    // graph.getNodes().forEach(it => {
    //   graph.clearItemStates(it)
    //   const model = it.getModel()
    //   if (nodesId.includes(model.id)) {
    //     // 选中的2起始点obvious
    //     if (model.id === startVertexId || model.id === endVertexId) {
    //       graph.setItemState(it, 'obvious', true)
    //     } else {
    //       graph.setItemState(it, 'actived', true)
    //     }
    //   } else {
    //     graph.setItemState(it, 'disabled', true)
    //   }
    // })
  }
  // 是否是最大或者最小
  // const getSorting = (params: any, type: string) => {
  //   if (['最大', '最小'].includes(params.conditionType)) {
  //     let {conditionType, value} = params
  //     let sorting = {
  //       type: type,
  //       direction: conditionType === '最大' ? 'desc' : 'asc',
  //       limit: value
  //     }
  //     return sorting
  //   }
  //   return null
  // }
  // 查找子图
  function onGraphFinish(values: any) {
    const vertexIdList = vertexlist?.map((item: any) => item.key)
    let edgeParamListParam: any = []
    let list = JSON.parse(JSON.stringify(edgeParamList))
    list.forEach((item: any, index: string) => {
      // 没有类型，不作为参数
      if (!item.edgeTypeName) {
        return
      }
      let {strengthCondition, countCondition} = item
      if (!strengthCondition.conditionType || !strengthCondition.value) {
        item.strengthCondition = {conditionType: null, value: null}
      }
      if (!countCondition.conditionType || !countCondition.value) {
        item.countCondition = {conditionType: null, value: null}
      }
      // item.sorting = getSorting(strengthCondition, 'strength') || getSorting(countCondition, 'count')
      edgeParamListParam.push(item)
    })
    let params: any = {step: values.step, vertexIdList, edgeParamList: edgeParamListParam}
    setGraphLoading(true)
    findSubGraphApi(params).then((res: any) => {
      // console.log(res.data)
      if (!res.data) {
        message.warn('未查询到数据')
        setGraphLoading(false)
        return
      }
      // 查询点保存
      store.dispatch(setSearchNodes({searchNodes: vertexIdList}))
      const {edgeList, vertexList} = res.data
      mergeData({
        highlight: true,
        edges: dealEdges(edgeList),
        nodes: dealNodes(vertexList)
      })
      setGraphLoading(false)
      setVisible(false)
    }).catch(e => {
      message.error('接口请求错误')
      console.error(e)
      setGraphLoading(false)
    })
  }

  useEffect(() => {
    if (!visible) {
      return
    }
    if (activeKey === 'path') {
      const selectedNodes = graph.findAllByState('node', 'selected')
      if (selectedNodes.length === 1) {
        const {id}: any = selectedNodes[0].getModel()
        form.setFieldsValue({
          startVertexId: id
        })
        setStartVertexId(id)
      } else if (selectedNodes.length === 2) {
        let start: any = selectedNodes[0].getModel()
        let end: any = selectedNodes[1].getModel()
        if (end.id !== lastNodeIdRef.current) {
          [start, end] = [end, start]
        }
        form.setFieldsValue({
          startVertexId: start.id,
          endVertexId: end.id
        })
        setStartVertexId(start.id)
        setEndVertexId(end.id)
      }
    } else if (activeKey === 'graph') {
      if (!selectorForm) {
        return
      }
      const selectedNodes = graph.findAllByState('node', 'selected')
      let options: any[] = []
      let values: any[] = []
      selectedNodes.forEach((it: any) => {
        const {id, style, type}: any = it.getModel()
        options.push({
          label: `${style.label.value}(${type})`,
          value: id
        })
        values.push({
          label: `${style.label.value}(${type})`,
          key: id
        })
      })
      selectorForm.init(options, values)

      if (values.length)  setVertexList(values)
    }
  }, [visible, graph, activeKey, form, selectorForm])

  useEffect(() => {
    setNativeActiveKey(activeKey)
  }, [activeKey])

  const handleActiveChange = useCallback((activeKey: string) => {
    setNativeActiveKey(activeKey)
  }, [])

  // select下拉多选
  function handleEdgeChange(value:any,option:any) {
    form.setFieldsValue({
      edgeTypeNameList: value
    })
  }
  // 查找子图-添加form
  // const addForm = () => {
  //   // let newForm = Form.useFormInstance  ()
  //   let list: any = [...edgeParamList]
  //   list.push({
  //     edgeTypeName: null,
  //     strengthCondition: {
  //       conditionType: null,
  //       value: ''
  //     },
  //     countCondition: {
  //       conditionType: null,
  //       value: ''
  //     }
  //   })
  //   setEdgeParamList(list)
  // }
  // 查询子图-删除form
  // const deleteForm = (index: number) => {
  //   const list = JSON.parse(JSON.stringify(edgeParamList))
  //   list.splice(index, 1)
  //   setEdgeParamList(list)
  //   const edgeTypeNameList = list.map((item: any) => item.edgeTypeName)
  //   setSelectedEdgeType([edgeTypeNameList])
  // }
  // 查找子图-类型修改
  const changeEdgeType = (value: string, index: number) => {
    const list = JSON.parse(JSON.stringify(edgeParamList))
    list[index].edgeTypeName = value
    setEdgeParamList(list)
    // 更新选中边类型
    const selectedType: any = list.map((item: any) => item.edgeTypeName)
    setSelectedEdgeType([...selectedType])
  }
  // 查找子图-强度次数数值修改
  const changeStrengthCount =  (value: string, index: number, type:string) => {
    let list = JSON.parse(JSON.stringify(edgeParamList))
    switch (type) {
      case 'strengthType':
        list[index].strengthCondition.conditionType = value
        break
      case 'strengthValue':
        list[index].strengthCondition.value = value
        break
      case 'countType':
        list[index].countCondition.conditionType = value
        break
      default:
        list[index].countCondition.value = value
    }
    setEdgeParamList(list)
  }
  // 子图edgeParamList初始化
  useEffect(() => {
    let list = [{
      edgeTypeName: null,
      strengthCondition: {
        conditionType: null,
        value: ''
      },
      countCondition: {
        conditionType: null,
        value: ''
      }
    }]
    setEdgeParamList(list)
  },[setEdgeParamList])
  if (!visible) {
    return null
  }
  const handleChange = (values: any) => {
    setVertexList(values)
  }
  const handleStartChange = (value: any) => {
    setStartVertexId(value)
    form.setFieldsValue({
      startVertexId: value
    })
  }
  const handleEndChange = (value: any) => {
    setEndVertexId(value)
    form.setFieldsValue({
      endVertexId: value
    })
  }

  return (
    <Modal maskClosable={false} title="探索" className="exploreWeb" visible={visible} onCancel={handleCancel} width={800} footer={null}>
      <Tabs defaultActiveKey="graph" activeKey={nativeActiveKey} onChange={handleActiveChange}>
        <TabPane tab="查找路径" key="path">
          <Form
            layout="vertical"
            form={form}
            onFinish={onPathFinish}
          >
            <Row gutter={30}>
              <Col span={12}>
                {/* 边类型 */}
                <EdgeTypeItem edgeTypeNameList={edgeTypeNameList} handleEdgeChange={handleEdgeChange}/>
              </Col>
              <Col span={12}>
                <Form.Item label="方向" name="direction" rules={[{required: true, message: '请选择方向'}]}>
                  <Select
                    style={{width: '100%'}}
                    placeholder="请选择方向"
                  >
                    {
                      directions.map(it => (
                        <Option key={it.value} value={it.value}>{it.label}</Option>
                      ))
                    }
                  </Select>
                </Form.Item>
              </Col>
            </Row>
            <Row gutter={30}>
              <Col span={12}>
                <Form.Item label="起点" name="startVertexId" rules={[{required: true, message: '请选择起点'}]}>
                  <Select
                    showSearch
                    onChange={handleStartChange}
                    optionFilterProp="label"
                  >
                  {data.nodes.map((it: any) => (
                    <Select.Option key={it.id} value={it.id} label={it.style?.label?.value}>
                      <div style={{display:"flex",alignItems: "center"}}>
                        {
                        <img style={{width:"15px",height:"15px",marginRight: "5px"}} src={it.style?.icon?.value} alt="" />
                        }
                        <span>{it.style?.label?.value}</span>
                      </div>
                    </Select.Option>))}
                  </Select>
                </Form.Item>
              </Col>

              <Col span={12}>
                <Form.Item label="终点" name="endVertexId" rules={[{required: true, message: '请选择终点'}]}>
                  <Select
                      showSearch
                      onChange={handleEndChange}
                      optionFilterProp="label"
                    >
                    {data.nodes.map((it: any) => (
                      <Select.Option key={it.id} value={it.id} label={it.style?.label?.value}>
                        <div style={{display:"flex",alignItems: "center"}}>
                          {
                          <img style={{width:"15px",height:"15px",marginRight: "5px"}} src={it.style.icon?.value} alt="" />
                          }
                          <span>{it.style?.label?.value}</span>
                        </div>
                      </Select.Option>))}
                    </Select>
                </Form.Item>
              </Col>
            </Row>
            <Row gutter={30}>
              <Col span={12}>
                <Form.Item label="搜索类型" name="searchPathType" rules={[{required: true, message: '请选择搜索类型'}]}>
                  <Select
                    style={{width: '100%'}}
                    placeholder="请选择搜索类型"
                  >
                    {
                      searchPathType.map(it => (
                        <Option key={it.value} value={it.value}>{it.label}</Option>
                      ))
                    }
                  </Select>
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item label="步数" name="maxStep" rules={[{required: true, message: '请输入步数'}]}>
                  <InputNumber style={{width: '100%'}} min={1} placeholder="请输入步数"/>
                </Form.Item>
              </Col>
            </Row>
            <Row gutter={30}>
              {/* <Col span={12}>
                <Form.Item label="结果数量" name="total" initialValue={5}>
                  <InputNumber style={{width: '100%'}} min={1} placeholder="请输入结果数量"/>
                </Form.Item>
                <br/>
              </Col> */}
            </Row>
            <Form.Item>
              <Button style={{width: '100%'}} type="primary" htmlType="submit">查找</Button>
            </Form.Item>
          </Form>
        </TabPane>
        {/* 全路径查询不显示连通性分析 */}
        {
        false &&
        <TabPane tab="连通性分析" key="graph">
          <Form
            ref={graphRef}
            layout="vertical"
            form={form1}
            onFinish={onGraphFinish}
          >
            <Form.Item label="点列表(请至少选择2个点列表)" name="vertexIdList" rules={[({ getFieldValue }) => ({
              validator(_, value) {
                  if (vertexlist?.length > 1) {
                    return Promise.resolve()
                  }
                  return Promise.reject(new Error('请至少选择2个点列表!'));
                }
              })]}>
              <DebounceSelect
                ref={setSelectorForm}
                mode="multiple"
                placeholder="请输入关键字"
                fetchOptions={findNode}
                maxTagCount="responsive"
                style={{
                  width: '100%',
                }}
                vertexlist={vertexlist}
                onChange={handleChange}
              />
            </Form.Item>
            <Form.Item label="步数" name="step" rules={[{required: true, message: '请输入步数'}]}>
              <InputNumber style={{width: '100%'}} min={1} placeholder="请输入步数"/>
            </Form.Item>
            {/* 边类型 强度 次数 */}

            <div style={{maxHeight: '300px',overflowY: 'scroll'}}>
            {
              edgeParamList.map((item: any,index: number) =>(
                  <div  className='form-group' key={index}>
                    <Form.Item label="类型" name={[index, "edgeTypeName"]} >
                      <>
                      <span style={{display: 'none'}}>{item.edgeTypeName}</span>
                      <Form.Item
                            name={['edgeTypeName']}
                            rules={[{required:true, message: '请选择类型'}]}
                          >
                            <Select
                              placeholder="请选择"
                              allowClear={true}
                              value={item.edgeTypeName}
                              onChange={(value) => {changeEdgeType(value, index)}}>
                                {
                                  edgeTypeNameList.map((it: any) => (
                                    <Option key={it.id} value={it.name} disabled={(item.edgeTypeName!==it.name) && selectedEdgeType.includes(it.name)}>{it.name}</Option>
                                  ))
                                }
                              </Select>
                          </Form.Item>
                      </>
                    </Form.Item>
                    <Form.Item label="强度" >
                      <Form.Item name={[index, 'strengthCondition', 'conditionType']}>
                        <>
                        <Select
                        placeholder="请选择"
                        allowClear={true}
                        value={item.strengthCondition.conditionType}
                        onChange={(value) => {changeStrengthCount(value, index, 'strengthType')}}>
                        {
                          graphStrengths.map((it: any) => (
                            <Option key={it.value} value={it.value} disabled={(['最大','最小'].includes(item.countCondition.conditionType))&&(['最大','最小'].includes(it.value))}>{it.label}</Option>
                          ))
                        }
                        </Select>
                        <span style={{display: 'none'}}>{item.strengthCondition.conditionType}</span>
                        </>
                      </Form.Item>
                      <Form.Item name={[index, 'strengthCondition', 'value']}>
                        <>
                        <InputNumber min={1} placeholder="请输入"  value={item.strengthCondition.value} onChange={(value) => {changeStrengthCount(value, index, 'strengthValue')}}/>
                        <span style={{display: 'none'}}>{item.strengthCondition.value}</span>
                        </>
                      </Form.Item>
                    </Form.Item>
                    <Form.Item label="次数">
                      <Form.Item name={[index, 'countCondition', 'conditionType']}>
                        <>
                        <Select
                        placeholder="请选择"
                        allowClear={true}
                        value={item.countCondition.conditionType}
                        onChange={(value) => {changeStrengthCount(value, index, 'countType')}}>{
                          graphStrengths.map((it: any) => (
                            <Option key={it.value} value={it.value} disabled={(['最大','最小'].includes(item.strengthCondition.conditionType))&&(['最大','最小'].includes(it.value))}>{it.label}</Option>
                          ))
                        }

                        </Select>
                        <span style={{display: 'none'}}>{item.countCondition.conditionType}</span>
                        </>
                      </Form.Item>
                      <Form.Item name={[index, 'countCondition', 'value']}>
                        <>
                        <InputNumber min={1} placeholder="请输入" value={item.countCondition.value} onChange={(value) => {changeStrengthCount(value, index, 'countValue')}}/>
                        <span style={{display: 'none'}}>{item.countCondition.value}</span>
                        </>
                      </Form.Item>
                    </Form.Item>
                    {/* 此功能暂时隐藏 */}
                    {/* <div className="end-icon">
                      {edgeParamList.length-1 === index && <PlusSquareOutlined onClick={addForm}/>}
                      {edgeParamList.length > 1 && <MinusSquareOutlined onClick={() => deleteForm(index)}/>}
                    </div> */}
                  </div>
              ))
            }
            </div>
            <Form.Item>
              <Button style={{width: '100%'}} type="primary" htmlType="submit" loading={graphLoading}>查找</Button>
            </Form.Item>
          </Form>
        </TabPane>
        }
      </Tabs>
    </Modal>
  )
}
