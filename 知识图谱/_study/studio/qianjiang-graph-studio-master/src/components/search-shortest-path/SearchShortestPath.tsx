import React, {useContext, useEffect, useImperativeHandle, useRef, useState} from "react"
import {message, Modal} from 'antd'
import {GraphinContext, GraphinData} from '@antv/graphin'
import {Algorithm, IG6GraphEvent} from '@antv/g6'
import store from '@/redux/store/index'
import { setSearchNodes } from '@/redux/action'


// @ts-ignore
const {findShortestPath} = Algorithm

type props = {
  data: GraphinData,
  mergeData: (data: any) => void,
  ref: any
}

const SearchShortestPath: React.FC<props> = React.forwardRef(({data,mergeData}, ref) => {
  const [dialogVisible, setDialogVisible] = useState(false)
  const [nodeNames, setNodeNames] = useState<string[]>([])
  const {graph} = useContext(GraphinContext)
  const lastNodeIdRef = useRef<string>('')

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

  useImperativeHandle(ref, () => ({
    onSearchPathToggle: (flag: boolean): boolean => {
      if (flag) {
        return findPath()
      } else {
        clearStates()
      }
      return true
    }
  }))

  const clearStates = () => {
    graph.getNodes().forEach((node: any) => {
      graph.clearItemStates(node)
    })
    graph.getEdges().forEach((edge: any) => {
      graph.clearItemStates(edge)
    })
  }

  const findPath = () => {
    const selectedNodes = graph.findAllByState('node', 'selected')
    let selectedId:any = selectedNodes.map((item:any) => item.getModel().id)
    let set:any = new Set(selectedId)
    selectedId = [...set]
    if (selectedId.length !== 2) {
      message.warn('请选择有且两个节点(使用Ctrl按键)')
      return false
    }
    clearStates()
    // path 为其中一条最短路径，allPath 为所有的最短路径
    const {path} = findShortestPath(data, selectedId[0], selectedId[1])
    setNodeNames([])
    const tempNodeNames: string[] = []
    let reverse = false
    if (!path) return false
    store.dispatch(setSearchNodes({searchNodes: [selectedId[0], selectedId[1]]}))

    path.forEach((id: string, index: number) => {
      const pathNode = data.nodes.filter(node => node.id === id)[0]
      if (index === 0 && id === lastNodeIdRef.current) {
        reverse = true
      }
      tempNodeNames.push(pathNode.style.label.value as string)
    })
    if (reverse) {
      tempNodeNames.reverse()
    }
    setNodeNames(tempNodeNames)
    const edges = []
    let nodesId = []
    data.edges.forEach((edge) => {
      path.forEach((nodeId, index)=> {
        const { source, target } = edge
        if ((source===nodeId&&target===path[index+1])||(target===nodeId&&source===path[index+1])) {
          edges.push(edge)
          nodesId.push(source)
          nodesId.push(target)
        }
      })
    })
    let set1: any = new Set(nodesId)
    nodesId = [...set1]
    const nodes = data.nodes.filter(node => nodesId.includes(node.id))
    mergeData({nodes, edges, highlight: true})
    setDialogVisible(true)
    return true
  }

  return (
    <Modal
    title="最短路径"
    visible={dialogVisible}
    maskClosable={false}
    onCancel={() => setDialogVisible(false)}
    footer={null}>
      {nodeNames.map((it, index) => (
      <span key={index}>
        {it + `${index !== nodeNames.length - 1 ? ' -> ' : ''}`}
      </span>))}
    </Modal>
  )
})

SearchShortestPath.displayName = 'SearchShortestPath'

export {SearchShortestPath}
