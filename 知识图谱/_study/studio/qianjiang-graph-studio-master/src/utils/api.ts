import axios from "axios";
import { dataURLtoBlob, getUrlParam } from "./index";
import qs from 'qs'
import store from '@/redux/store/index'
import request from '@/utils/request'

// 获取图
const getGraph = (id: string) => {
  return new Promise<any>((resolve) => {
    request({
      url: `base/relationalGraph/data/${id}`,
      method: 'get'
    }).then((response) => {
      resolve(response.data)
    })
  })
}

// 获取图库列表
const getLibraryList = (params: any) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve) => {
    request({
      url: 'base/library/queryLibraryListByGroupId',
      method: 'get',
      headers: { spaceId },
      params
    }).then((response) => {
      resolve(response.data)
    })
  })
}


// 更新
const updateLibrary = (data) => {
  return new Promise<any>((resolve) => {
    request({
      url: 'base/relationalGraph/data/update',
      method: 'post',
      data
    }).then((response) => {
      resolve(response.data)
    })
  })
}

// 保存图片
const saveGraphImage = (base64: string) => {
  const formData = new FormData()
  formData.append('file', dataURLtoBlob(base64))
  return new Promise<any>((resolve) => {
    request({
      url: 'file/upload/batch',
      method: 'post',
      data: formData,
      headers: {
        'Content-Type': 'multipart/form-data'
      }
    }).then((response) => {
      resolve(response.data)
    })
  })
}

// 获取图库树列表
const getLibrarTree = () => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve) => {
    request({
      url: 'base/library/tree',
      method: 'get',
      headers: { spaceId }
    }).then((response) => {
      resolve(response.data)
    })
  })
}

// 查询分组名下拉
const getGroupList = (params?: any) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve) => {
    request({
      url: 'base/library/group/list',
      method: 'get',
      headers: { spaceId },
      params
    }).then((response) => {
      resolve(response.data)
    })
  })
}

// 新增过程库
const addProcess = (data: any) => {
  const spaceId = store.getState().spaceId
  if (data.groupId === '关联图谱') {
    data.groupId = '0'
  }
  return new Promise<any>((resolve) => {
    request({
      url: 'base/library/add/',
      method: 'post',
      headers: { spaceId },
      data
    }).then((response) => {
      resolve(response.data)
    })
  })
}

// 编辑过程库
const editProcess = (data: any) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve) => {
    request({
      url: 'base/library/update',
      method: 'post',
      headers: { spaceId },
      data
    }).then((response) => {
      resolve(response.data)
    })
  })
}

// 删除过程库
const deleteProcess = (id: string) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve) => {
    request({
      url: 'base/library/delete/' + id,
      method: 'delete',
      headers: { spaceId }
    }).then((response) => {
      resolve(response.data)
    })
  })
}

// 删除分组
const deleteGroup = (id: string) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve) => {
    request({
      url: 'base/library/group/delete/' + id,
      method: 'delete',
      headers: { spaceId }
    }).then((response) => {
      resolve(response.data)
    })
  })
}

// 分组详情数据
const viewGroup = (id: string) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve) => {
    request({
      url: 'base/library/' + id,
      method: 'get',
      headers: { spaceId }
    }).then((response) => {
      resolve(response.data)
    })
  })
}

// 新增分组
const addGroup = (data: any) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve) => {
    request({
      url: 'base/library/group/add',
      method: 'post',
      headers: { spaceId },
      data
    }).then((response) => {
      resolve(response.data)
    })
  })
}

// 编辑分组
const editGroup = (data: any) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve) => {
    request({
      url: 'base/library/group/update',
      method: 'post',
      headers: { spaceId },
      data
    }).then((response) => {
      resolve(response.data)
    })
  })
}

// 拖拽修改过程库
const updateProcess = (data: any) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve) => {
    request({
      url: 'base/library/drag',
      method: 'post',
      headers: {
        spaceId,
        'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8;'
      },
      data: qs.stringify(data)
    }).then((response) => {
      resolve(response.data)
    })
  })
}

// 拖拽修改多个过程库
const updateProcessList = (data: any) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve) => {
    request({
      url: 'base/library/drag/batch',
      method: 'post',
      headers: {
        spaceId,
        'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8;'
      },
      data: qs.stringify(data)
    }).then((response) => {
      resolve(response.data)
    })
  })
}

// 拖拽修改分组
const updateGroup = (data: any) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve) => {
    request({
      url: 'base/library/group/drag',
      method: 'post',
      headers: {
        spaceId,
        'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8;'
      },
      data: qs.stringify(data)
    }).then((response) => {
      resolve(response.data)
    })
  })
}

// 查询关系图谱
const getAssociateImage = (params: any) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve) => {
    request({
      url: '/base/relationalGraph/selectGraphPageByLibraryId',
      method: 'get',
      headers: {
        spaceId,
        'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8;'
      },
      params
    }).then((response) => {
      resolve(response.data)
    })
  })
}

// 更新关联图谱
const updateAssociateImage = (data: Object) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve) => {
    request({
      url: '/base/relationalGraph/update',
      method: 'post',
      headers: {
        spaceId
      },
      data
    }).then((response) => {
      resolve(response.data)
    })
  })
}

// 删除关联图谱
const deleteAssociateImage = (id: string) => {
  const spaceId = store.getState().spaceId
  return new Promise<any>((resolve) => {
    request({
      url: '/base/relationalGraph/delete/' + id,
      method: 'post',
      headers: {
        spaceId,
        'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8;'
      }
    }).then((response) => {
      resolve(response.data)
    })
  })
}

// 过程库弹框-选择分组名查询过程库
const queryLibraryListByGroupId = (params) => {
  return new Promise<any>((resolve, reject) => {
    axios({
      url: `${window.location.protocol}//${window.location.hostname}:${getUrlParam("port")}/base/library/queryLibraryListByGroupId`,
      method: 'get',
      params
    }).then((response) => {
      resolve(response.data)
    }).catch(err => {
      reject(err)
    }).finally(() => {
      resolve({})
    })
  })
}
// 获取字典
const getDicts = (dictType) => {
  return new Promise<any>((resolve, reject) => {
    axios({
      url: `${window.location.protocol}//${window.location.hostname}:${getUrlParam("port")}/system/dict/data/type/${dictType}`,
      method: 'get'
    }).then((response) => {
      resolve(response.data)
    }).catch(err => {
      reject(err)
    }).finally(() => {
      resolve({})
    })
  })
}


const exportObject = {
  getGraph,
  getLibraryList,
  saveGraphImage,
  updateLibrary,
  getLibrarTree,
  getGroupList,
  addProcess,
  editProcess,
  viewGroup,
  deleteProcess,
  deleteGroup,
  addGroup,
  editGroup,
  updateProcess,
  updateProcessList,
  updateGroup,
  getAssociateImage,
  updateAssociateImage,
  deleteAssociateImage,
  queryLibraryListByGroupId,
  getDicts
}

export default exportObject
