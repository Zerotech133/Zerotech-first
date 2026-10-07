import store from "@/redux/store"

export default class mergeDataMethod {
  // 矫正撤销导致的数据错误
  // static initCurrentData(dataCopy) {
  //   let currentData: any = store.getState().currentData
  //   if (currentData?.nodes) {
  //     if (currentData?.nodes.length !== dataCopy.nodes.length || currentData.edges.length !== dataCopy.edges.length) {
  //       dataCopy.nodes = currentData.nodes
  //       dataCopy.edges = currentData.edges
  //     }
  //   }
  // }
  // 去掉集合产生的node edge，恢复原node edge，与新的接口数据拼接展示新页面
  static initData(hideMap, dataCopy) {
    const gatherNodesId = Object.keys(hideMap)
    for (let prop in hideMap) {
      let nodes1 = dataCopy.nodes.filter(node => !gatherNodesId.includes(node.id))
      dataCopy.nodes = nodes1.concat(hideMap[prop].hideNodes)
      // 起点中心点，终点聚合点的边去掉
      let edges1 = dataCopy.edges.filter((edge => {
        const { source, target } = edge
        if (gatherNodesId.includes(source)&&target===hideMap[source].maxNodeId) {
          return false
        }
        if (gatherNodesId.includes(target)&&source===hideMap[target].maxNodeId) {
          return false
        }
        return true
      }))
      // 起点普通点，终点集合的的边恢复
      edges1.forEach(item => {
        const { source, target } = item
        if (source.includes('gatherNodeId')) {
          item.source = item.attrs['起点']
        }
        if (target.includes('gatherNodeId')) {
          item.target = item.attrs['终点']
        }
      })
      dataCopy.edges = edges1.concat(hideMap[prop].hideEdges)
    }
  }
  static recoverEdges(dataCopy, newNodeIds) {
    dataCopy.edges.forEach(edge => {
      if (edge.attrs) {
        if (newNodeIds.includes(edge.attrs['起点'])) {
          edge.source = edge.attrs['起点']
        }
        if (newNodeIds.includes(edge.attrs['终点'])) {
          edge.target = edge.attrs['终点']
        }
      }
    })
  }
  // 隐藏点边上图
  static addHideData(dataCopy, newHideNodes) {
    newHideNodes.forEach(newHideNode => {
      dataCopy.nodes.forEach(node => {
        if (newHideNode.id === node.id) {
          const { hideNodesIdNum,  hideEdgesIdNum, maxNodeLabel} = newHideNode
          node.hideNodesIdNum = hideNodesIdNum
          node.hideEdgesIdNum = hideEdgesIdNum
          node.label = `隐藏点个数:${hideNodesIdNum}\n隐藏边个数:${hideEdgesIdNum}\n中心点: ${maxNodeLabel}`
        }
      })
      // 隐藏点边全部上图后,聚合点删除
      if (newHideNode.hideEdgesIdNum === 0) {
        dataCopy.nodes = dataCopy.nodes.filter(item => item.id !== newHideNode.id)
        // 起点在集合里，终点非中心点的边释放
        dataCopy.edges.forEach(item => {
          if (item.source === newHideNode.id && item.attrs) {
            item.source = item.attrs['起点']
          }
          if (item.target === newHideNode.id && item.attrs) {
            item.target = item.attrs['终点']
          }
        })
        dataCopy.edges = dataCopy.edges.filter(item => item.source !== newHideNode.id && item.target !== newHideNode.id)
      }
    })
  }
  // 处理新上图的点，添加样式
  static newNodesDeal(dataCopy,nodes,getNodeStyle) {
    const currentNodeIds = dataCopy.nodes.map(it => it.id)
    nodes = nodes.slice().filter(it => !currentNodeIds.includes(it.id))
    nodes.forEach((it: any) => {
      if (it?.attrs?.suspectFlag) {
        it.attrs.suspectFlag1 = ['true', true, '嫌疑人'].includes(it.attrs.suspectFlag) ? '嫌疑人' : '其他'
      }
      it.style = {
        label: it.style?.label,
        keyshape: getNodeStyle(it),
        icon: {
          type: 'image',
          value: it.iconPath,
          size: [20, 20]
        }
      }
      it.attrs.suspectFlag = ['true', true].includes(it.attrs.suspectFlag) ? '嫌疑人' : '其他'
    })
    return { nodes}
  }
  // 处理新上图的边，添加样式
  static newEdgesDeal(dataCopy,edges,defaultEdgeStyle ){
    const currentEdgesIds = dataCopy.edges.map(it => it.source + '-' + it.target + '-' + it.edgeTypeName + '-' + it.strength + '-' + it.count)

    let newEdgeIds: string[] = edges.map(it => it.source + '-' + it.target)
    edges = edges.slice().filter(it => !currentEdgesIds.includes(it.source + '-' + it.target + '-' + it.edgeTypeName + '-' + it.strength + '-' + it.count))
    edges.forEach((it: any) => {
      it.style = {
        keyshape: Object.assign({}, defaultEdgeStyle, it.style?.keyshape),
        label: it.style?.label
      }
    })
    return { newEdgeIds, edges}
  }
  // 1计算每个点对应边的数量,2筛选出连接边多的点
  static nodeToEdges(graphData, maxNum) {
    const nodesMap: any = {}
    graphData.edges.forEach(item => {
      const {source, target} = item
      if (nodesMap[source]) {
        nodesMap[source] = nodesMap[source] + 1
      } else {
        nodesMap[source] = 1
      }
      if (nodesMap[target]) {
        nodesMap[target] = nodesMap[target] + 1
      } else {
        nodesMap[target] = 1
      }
    })
    const maxNodeIds = []
    for(let prop in nodesMap) {
      if (nodesMap[prop] > maxNum) {
        maxNodeIds.push(prop)
      }
    }
    return maxNodeIds
  }
  // 是否查询前已经有hideMap数据，有的话新数据需要显示
  static isDoubleSearch(hideMap,highlight) {
    const length = Object.keys(hideMap).length
    return length>0 && highlight
  }
  // 筛选可以隐藏的点(嫌疑人，搜索点不能隐藏)
  static getTotalHideNodes(maxNodeIds, showNodesId, key, graphData) {
    let totalHideNodesId = []
    maxNodeIds.forEach(maxNodeId => {
      // 计算隐藏点边(嫌疑人不能隐藏)
      graphData.edges.forEach(edge => {
        let {source, target} = edge
        if (source !== key && target !== key) {// 查询点不隐藏 嫌疑人不隐藏//
          if (source===maxNodeId&&target!==maxNodeId&&!showNodesId.includes(target)&&
          !target.includes('gatherNodeId')) {
            totalHideNodesId.push(target)
          }
          if (target===maxNodeId&&source!==maxNodeId&&!showNodesId.includes(source)&&
          !source.includes('gatherNodeId')) {
            totalHideNodesId.push(source)
          }
        }
      })
    })
    // 边2侧的点不能同时隐藏
    graphData.edges.forEach(edge => {
      let {source, target} = edge
      if (totalHideNodesId.includes(source)&&totalHideNodesId.includes(target)) {
        totalHideNodesId = totalHideNodesId.filter(hideNodeId => hideNodeId!==source && hideNodeId!==target)
      }
    })
    return totalHideNodesId
  }
  // 定义聚合点id
  static defineGatherNodeId(gatherNodesIdInner) {
    let gatherNodeId:string
    // 聚合点后缀数字次序递增
    if (gatherNodesIdInner.length) {
      let lastGatherNodeId = gatherNodesIdInner[gatherNodesIdInner.length - 1]
      let lastIndex = lastGatherNodeId.slice(12)
      let newIndex = Number(lastIndex) + 1
      gatherNodeId = 'gatherNodeId' + newIndex
    } else {
      gatherNodeId = 'gatherNodeId1'
    }
    return gatherNodeId
  }
  // 筛选中心点对应隐藏点边id
  static getHideNodesEdgesId(graphData,maxNodeId,maxNodeIds,totalHideNodesId,searchNodes) {
    // 计算隐藏点边(嫌疑人不能隐藏)
    let hideNodesId = [], hideEdgesId = []
    graphData.edges.forEach(edge => {
      let {source, target} = edge
      if (source===maxNodeId&&target!==maxNodeId&&!searchNodes.includes(target)&&
      !maxNodeIds.includes(target)&&!target.includes('gatherNodeId')&&totalHideNodesId.includes(target)) {//&&!suspectNodesId.includes(target)
        hideNodesId.push(target)
        hideEdgesId.push(source+'-'+target)
      }
      if (target===maxNodeId&&source!==maxNodeId&&!searchNodes.includes(source)&&
      !maxNodeIds.includes(source)&&!source.includes('gatherNodeId')&&totalHideNodesId.includes(source)) {//&&!suspectNodesId.includes(source)
        hideNodesId.push(source)
        hideEdgesId.push(source+'-'+target)
      }
    })
    let set: any = new Set(hideNodesId)
    hideNodesId = [...set]
    return { hideNodesId, hideEdgesId }
  }
  // 筛选中心点对应隐藏点，显示点
  static getHideNodes(graphData, hideNodesId, maxNodeId) {
    let showNodes = [],hideNodes=[],maxNodeLabel = ''
    graphData.nodes.forEach(node => {
      if (hideNodesId.includes(node.id)){
        hideNodes.push(node)
      } else {
        showNodes.push(node)
      }
      // 聚集点label
      if (node.id === maxNodeId) {
        const value = node.style.label.value
        const arr = value.split('\n')
        maxNodeLabel = arr[0]
      }
    })
    return {showNodes, hideNodes, maxNodeLabel}
  }
  // 定义中心点对应-聚合点
  static defineGatherNode(hideNodesId, hideEdgesId, maxNodeLabel, gatherNodeId,maxNodeId) {
    return {
      x: 150,
      y: 150,
      hideNodesIdNum: hideNodesId.length,
      hideEdgesIdNum: hideEdgesId.length,
      maxNodeLabel,
      label: `隐藏点个数:${hideNodesId.length}\n隐藏边个数:${hideEdgesId.length}\n中心点: ${maxNodeLabel}`,
      id: gatherNodeId,
      maxNodeId,
      type: 'rect',
      size: [210, 65],
      stateStyles: {
        actived: {
          stroke: 'red'
        },
        selected: {
          stroke: '#40a9ff',
          fill: '#87CEFA'
        },
        obvious: {
          stroke: 'red'
        }
      },
      attrs: {
        caseInfoList: "[{}]",
        suspectFlag: "其他",
      },
      comboId: [],
      style: {
        icon: {type: '', value: ''},
        label: {
          value: ''
        },
        fill: '#ffffff',
        cursor: 'pointer'
      }
    }
  }
  // 定义中心点对应-聚合边
  static defineGatherEdge(graphData,hideEdgesId,hideNodesId,gatherNodeId) {
    let showEdges = [],hideEdges=[]
    graphData.edges.forEach(edge => {
      let {source, target} = edge
      let sourceTarget = source+'-'+target
      if (hideEdgesId.includes(sourceTarget)) {
        hideEdges.push(edge)
      } else {
        if (hideNodesId.includes(source)&&!target.includes('gatherNodeId')) {
          edge.source = gatherNodeId
        }
        if (hideNodesId.includes(target)&&!source.includes('gatherNodeId')) {
          edge.target = gatherNodeId
        }
        showEdges.push(edge)
      }
    })
    return { showEdges, hideEdges }
  }
  // 新上图点边高亮
  static highlightNodesEdges(graph,newNodeIds,newEdgeIds,ignodeHideData,isDoubleSearch) {//newEdgeIds,newNodeIds
    graph.getEdges().forEach((edge) => {
      graph.clearItemStates(edge)
      const model = edge.getModel()
      // 边2侧点id连接
      let edgeIds
      if (model.source.includes('gatherNodeId')||model.target.includes('gatherNodeId')) {
        if(model.attrs) {
          edgeIds = `${model.attrs['起点']}-${model.attrs['终点']}`
        }
      } else {
        edgeIds = `${model.source}-${model.target}`
      }
      if (newEdgeIds.includes(edgeIds)) {
        graph.setItemState(edge, 'actived', true)
      } else if (!isDoubleSearch&&!model.attrs) {
        graph.setItemState(edge, 'actived', true)
      } else {
        graph.setItemState(edge, 'disabled', true)
      }
    })
    const searchNodes: any = store.getState().searchNodes
    graph.getNodes().forEach((node) => {
      graph.clearItemStates(node)
      const model: any = node.getModel()
      if (searchNodes.includes(model.id)&&!ignodeHideData) {
        graph.setItemState(node, 'obvious', true)
      } else if (newNodeIds.includes(model.id)) {
        graph.setItemState(node, 'actived', true)
      } else if(!isDoubleSearch&&model.id.includes('gatherNodeId')) {
        graph.setItemState(node, 'actived', true)
      }else {
        graph.setItemState(node, 'disabled', true)
      }
    })
  }
}
