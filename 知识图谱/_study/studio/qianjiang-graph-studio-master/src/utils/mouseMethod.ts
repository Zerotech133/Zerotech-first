import { message } from 'antd'
import { storeHideMapList } from '@/utils'

export default class mouseMethod {
  // 复制
  static ctrlCMethod(nodes, edges, hideMap){
    edges = JSON.parse(JSON.stringify(edges))
    // const nodesId = nodes.map(item => item.id)
    let gatherNodesId = []
    getGatherNodes(nodes, edges, gatherNodesId)
    // 独立集合点不需要处理
    let gatherNodesId1 = []
    edges.forEach(edge => {
      const { source, target } = edge
      if (gatherNodesId.includes(source)) {
        gatherNodesId1.push(source)
      }
      if (gatherNodesId.includes(target)) {
        gatherNodesId1.push(target)
      }
    })
    // 与点相连的集合点释放，多余点删除
    if(gatherNodesId1.length) {
      // 需要释放的点id
      const nodesId1 = recoverEdges(edges,gatherNodesId1)
      if (nodesId1) {
        for(let gatherNodeId in hideMap) {
          if (gatherNodesId1.includes(gatherNodeId)){
            const nodes1 = hideMap[gatherNodeId].hideNodes.filter(node => nodesId1.includes(node.id))
            // 删除集合点
            nodes = nodes.concat(nodes1)
          }
        }
        nodes = nodes.filter(item => !gatherNodesId1.includes(item.id))
      }
    }
    return {nodes, edges}
  }
  // 粘贴
  static ctrlVMethod(nodesData,dataCopy) {
    let { nodes, edges } = JSON.parse(nodesData)
    // const nodesId = nodes.map(item => item.id)
    const gatherNodesId = []
    let hideMapStorage:any = localStorage.getItem('hideMap')
    if (hideMapStorage) {
      hideMapStorage = JSON.parse(hideMapStorage)
    }

    getGatherNodes(nodes, edges,gatherNodesId)
    // 复制时没有把聚合点的中心点带过来，聚合点恢复成点
    if(gatherNodesId.length) {
      recoverEdges(edges,gatherNodesId)
      nodes = deleteHideMap(gatherNodesId, hideMapStorage, nodes)
    }
    return { nodes, edges, hideMapStorage }
  }
  // 删除
  static handelRemoveNode(graph,setHideMap,setData, setBindGraphData, hideMap,data) {
    let selectedNodes=graph.findAllByState('node', 'selected')
    if(selectedNodes.length>0){
      let nodesId = selectedNodes.map((item: any) => item.getID())
      let nodes = data.nodes.filter(it => nodesId.indexOf(it.id) === -1)
      let edges = data.edges.filter(it => nodesId.indexOf(it.source) === -1&& nodesId.indexOf(it.target) === -1)
      const nodesEdges = deleteNodesEdges(nodes,edges,hideMap )
      if (nodesEdges) {
        nodes = nodesEdges.nodes
        edges = nodesEdges.edges
        setHideMap(nodesEdges.hideMap)
        storeHideMapList(nodesEdges.hideMap, {nodes,edges})
      } else {
        storeHideMapList(hideMap, {nodes,edges})
      }
      setData({nodes,edges})
      setBindGraphData({nodes,edges})
    }else{
      message.warn('请先选择要删除的节点')
    }
  }
  static getGraphData(graphData, hideMap){
    let totalNodes = graphData.nodes.filter(item => !item.id.includes('gatherNode'))
    const gatherNodesId: any = Object.keys(hideMap)
    let totalEdges = graphData.edges.filter((item: any) => {
      const { source, target } = item
      // 过滤中心点+集合点的边
      if (gatherNodesId.includes(source)&&target===hideMap[source].maxNodeId) {
        return false
      }
      if (gatherNodesId.includes(target)&&source===hideMap[target].maxNodeId) {
        return false
      }
      return true
    })
    totalEdges = JSON.parse(JSON.stringify(totalEdges))
    totalEdges.forEach(item => {
      const { source, target, attrs } = item
      if (source.includes('gatherNodeId')&&attrs) {
        item.source = attrs['起点']
      }
      if (target.includes('gatherNodeId')&&attrs) {
        item.target = attrs['终点']
      }
    })
    for(let gatherNodeId in hideMap) {
      const { hideNodes, hideEdges } = hideMap[gatherNodeId]
      totalNodes = totalNodes.concat(hideNodes)
      totalEdges = totalEdges.concat(hideEdges)
    }
    return {nodes: totalNodes, edges: totalEdges}
  }
  // 根据查询边获取上图点跟边
  static getNewNodesEdges(data, keys) {
    const edges = data.edges.filter(edge => keys.includes(edge.id))
    let nodesId = []
    edges.forEach(edge => {
      nodesId.push(edge.source)
      nodesId.push(edge.target)
    })
    let set: any = new Set(nodesId)
    nodesId = [...set]
    const nodes = data.nodes.filter(node => nodesId.includes(node.id))
    return {nodes, edges}
  }
}
function deleteNodesEdges(nodes,edges,hideMap) {
  // const nodesId = nodes.map(item => item.id)
  const gatherNodesId = []

  getGatherNodes(nodes, edges,gatherNodesId)

  if(gatherNodesId.length) {
    recoverEdges(edges,gatherNodesId)
    nodes = deleteHideMap(gatherNodesId, hideMap, nodes)
    return { nodes, edges, hideMap }
  } else {
    return null
  }

}
// 查询没有中心点的聚合点
function getGatherNodes(nodes, edges, gatherNodesId) {
  nodes.forEach(node => {
    const { id, maxNodeId } = node
    if (id.includes('gatherNodeId')) {
      // 没有线与中心点连接
      const arr = edges.filter(edge => (edge.source===id&&edge.target===maxNodeId) ||(edge.source===maxNodeId&&edge.target===id))
      if (arr.length===0) {
        gatherNodesId.push(id)
      }
    }
  })
}
// 恢复与聚合点连接的边
function recoverEdges(edges: any,gatherNodesId: any) {
  const nodesId = []
  edges.forEach(edge => {
    const { source, target, attrs } = edge
    if (gatherNodesId.includes(source)) {
      edge.source = attrs['起点']
      nodesId.push(attrs['起点'])
    }
    if (gatherNodesId.includes(target)) {
      edge.target = attrs['终点']
      nodesId.push(attrs['终点'])
    }
  })
  return nodesId
}

// 删掉没有中心点的聚合点,聚合点释放
function deleteHideMap(gatherNodesId, hideMapStorage, nodes) {
  for(let gatherNodeId in hideMapStorage) {
    if (gatherNodesId.includes(gatherNodeId)){
      nodes = nodes.concat(hideMapStorage[gatherNodeId].hideNodes).filter(node => !gatherNodesId.includes(node.id))
      delete hideMapStorage[gatherNodeId]
    }
  }
  return nodes
}


