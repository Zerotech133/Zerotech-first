import store from '@/redux/store/index'
import labelSvg from '@/assets/svg/label.svg'
import { setHideMapList } from '@/redux/action/index'

/**
 * base64 转 Blob，用于上传
 * @param base64
 */
export const dataURLtoBlob = (base64: string) => {
  const arr = base64.split(',')
  if (!arr.length) {
    return ''
  }
  const matchResult = arr[0].match(/:(.*?);/)
  if (!matchResult || !matchResult[1]) {
    return ''
  }
  const mime = matchResult[1]
  const bstr = atob(arr[1])
  let n = bstr.length
  const u8arr = new Uint8Array(n)
  while (n--) {
    u8arr[n] = bstr.charCodeAt(n)
  }
  return new Blob([u8arr], { type: mime })
}
export const handleTree = (data: any, id: any, parentId?: any, children?: any, rootId?: any, dropId = '') => {
  id = id || 'id'
  parentId = parentId || 'parentId'
  children = children || 'children'
  rootId = rootId || Math.min(...data.map((item: any) => {
    return item[parentId]
  }))
  // 对源数据深度克隆
  const cloneData = JSON.parse(JSON.stringify(data))
  // 循环所有项

  const treeData = cloneData.filter((father: any) => {
    const branchArr = cloneData.filter((child: any) => {
      child.key = child[id]
      child.title = child.name
      // 返回每一项的子级数组
      return father[id] === child[parentId] && child[id] !== dropId
    })
    branchArr.length > 0 ? father.children = branchArr : delete (father.children)
    // 返回第一层
    return father[parentId] === rootId
  })
  return treeData !== '' ? treeData : data
}
export const dealNodes = (nodes: any) => {
  const iconPathList: any = store.getState().iconPathList

  let nodeList = nodes.map((it: any) => ({
    id: it.id,
    attrs: { ...it.propertyList },
    style: {
      label: {
        value: it.name,
        offset: [0, 10]
      }
    },
    iconPath: iconPathList[it.tagName],
    tagName: it.tagName
  }))
  return nodeList
}
export const dealEdges = (edges: any) => {
  let arr: any = []
  let singleEdges: any = []
  // 重复边去重
  edges.forEach((item: any) => {
    let str = item.sourceId + '-' + item.destinationId + '-' + item.edgeTypeName
    if (arr.indexOf(str) === -1) {
      arr.push(str)
      singleEdges.push(item)
    }
  })
  let edgeList = singleEdges.map((it: any) => {
    let { sourceId, destinationId, edgeTypeName, strength, count } = it
    let edgeTypeName1 = edgeTypeName ? '连接类型:' + edgeTypeName : ''
    let strength1 = strength ? ' 强度:' + strength : ''
    let count1 = count ? ' 次数:' + count : ''
    return {
      source: sourceId,
      target: destinationId,
      attrs: {
        '起点': sourceId,
        '终点': destinationId,
        '边类型': edgeTypeName,
        '强度': String(strength),
        '次数': String(count)
      },
      strength,
      count,
      edgeTypeName,
      style: {
        label: {
          value: edgeTypeName1 + strength1 + count1,
          fill: 'gray'
        }
      }
    }
  })
  return edgeList
}
// 处理单选下拉框数据
export const dealSelectData = (data: any, id: string, name: string) => {
  return data.map((item: any) => {
    return {
      value: item[id],
      label: item[name],
      ...item
    }
  })
}
// 强度次数是最大或者最小
export const getSorting = (params: any, type: string, edgeTypeName?: string) => {
  if (['最大', '最小'].includes(params.conditionType)) {
    let { conditionType, value } = params
    let sorting: any = {
      type: type,
      direction: conditionType === '最大' ? 'desc' : 'asc',
      limit: value
    }
    if (edgeTypeName) sorting.propertyName = edgeTypeName
    return sorting
  }
  return null
}

export const getUrlParam = (paraName: string) => {
  const url = document.location.toString();
  const arrObj = url.split("?");

  if (arrObj.length > 1) {
    const arrPara = arrObj[1].split("&");
    let arr;

    for (let i = 0; i < arrPara.length; i++) {
      arr = arrPara[i].split("=");

      if (arr != null && arr[0] === paraName) {
        return arr[1];
      }
    }
    return "";
  } else {
    return "";
  }
}
// 显示嫌疑人标签图标
export const showSuspectLabel = (graph: any) => {
  graph.getNodes().forEach((node: any) => {
    const model = node.getModel()
    const suspicionTag = model.attrs?.suspicionTag
    if (suspicionTag) {
      const group = node.get('group')
      group.addShape('image', {
        attrs: {
          img: labelSvg,
          x: 4,
          y: -20,
          width: 20,
          height: 20
        }
      });
    }
  })
}
function addZero(s){
  return s < 10 ? ('0' + s) : s
}
export const getNowTime = () => {
  const date = new Date()
  const year = date.getFullYear()
  const mouth = date.getMonth() + 1
  const day = date.getDate()
  const hour = date.getHours()
  const minute = date.getMinutes()
  const second = date.getSeconds()
  const time = year + addZero(mouth) + addZero(day) + addZero(hour) + addZero(minute) + addZero(second)
  return time
}
export const getTaskTime = () => {
  const date = new Date()
  const year = date.getFullYear()
  const mouth = date.getMonth() + 1
  const day = date.getDate()
  const hour = date.getHours()
  const minute = date.getMinutes()
  const second = date.getSeconds()
  const time = `${year}/${mouth}/${day} ${addZero(hour)}:${addZero(minute)}:${addZero(second)}`
  return time
}
// 把隐藏的点边存起来，撤销重做的时候用
export const storeHideMapList = (hideMap, data) => {
  if (data?.nodes) {
    const hideMapList = store.getState().hideMapList
    const gatherNodesId = data.nodes?.filter(item => item.id.includes('gatherNodeId'))

    if (gatherNodesId.length) {
      const edgesId = data.edges.map(item => item.source + '-' + item.target).join(',')
      hideMapList.push({
        edgesId,
        hideMap
      })
      store.dispatch(setHideMapList({ 'hideMapList': hideMapList }))
    }
  }
}
// 撤销重做时，恢复hideMap
export const recoverHideMap = (nodes, edges, setHideMap) => {
  if (edges?.length) {
    const gatherNodesId = []
    nodes.forEach(item => {
      let model = item.getModel()
      gatherNodesId.push(model.id)
    })
    if (gatherNodesId.length) {
      const hideMapList = store.getState().hideMapList
      const edgesIdList = []
      edges.forEach(item => {
        const model = item.getModel()
        const { source, target } = model
        const edge = source + '-' + target
        edgesIdList.push(edge)
      })
      const edgesId = edgesIdList.join(',')
      // const edgesId = edges.map(item => item.cfg.model.source + '-' + item.cfg.model.target).join(',')
      const hideMap = hideMapList.filter(item => item.edgesId === edgesId)[0]?.hideMap
      if (hideMap) {
        setHideMap(hideMap)
      }
    } else {
      setHideMap({})
    }
  } else {
    setHideMap({})
  }
}
// 撤销过程中,调用setData前，需要恢复data数据
export const recoverData = (currentData,data, graph) => {
  let nodes = graph.getNodes()
  let edges = graph.getEdges()
  // data与图上实际数据不符
  if(data?.nodes?.length !== nodes?.length || data?.edges?.length !== edges?.length) {
    // const currentData = store.getState().currentData
    // 栈数据与图上实际数据相同
    if (currentData?.nodes?.length === nodes?.length && currentData?.edges?.length === edges?.length) {
      return currentData
    } else {// 栈数据与图上实际数据不同，取实际数据
      nodes = nodes.map(item => item.getModel())
      edges = edges.map(item => item.getModel())
      return {nodes, edges}
    }
  }
  return data
}
