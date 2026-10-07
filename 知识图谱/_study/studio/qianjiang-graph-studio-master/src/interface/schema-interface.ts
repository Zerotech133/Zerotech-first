// 查询空间列表
export interface getSpaceListInterface {
  pageNum: number,
  pageSize: number
}

// 创建空间列表
export interface spaceCreateInterface {
  name: string
}
// 删除空间列表
export interface deleteSpaceInterface {
  id: string
}