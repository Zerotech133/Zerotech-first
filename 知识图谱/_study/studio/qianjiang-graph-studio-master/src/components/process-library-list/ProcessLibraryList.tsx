import React, {useCallback, useEffect, useState} from "react"
import {Input, Button} from "antd"
import styles from './ProcessLibraryList.module.scss'
import addGroupSvg from '../../assets/svg/add-group.svg'
import doubleArrowSvg from '../../assets/svg/double-arrow.svg'
import { OriginLibraryTree } from "./origin-library-tree"
import { AddProcessLibrary } from "./add-process-library"
import {EditGroup} from './edit-group'
import API from '../../utils/api'
import { handleTree } from "../../utils"

type props = {
  saveGraphNum: number
}

export  const ProcessLibraryList: React.FC<props> = ({saveGraphNum}) => {
  
  // 搜索内容
  const [searchValue, setSearchValue] = useState('')
  // 树顶部文字是否显示
  const [treeTopShow] = useState(false)
  // 未处理的原始库数据
  const [originLibraryList] = useState<any>([])
  // 处理成树形结构后的原始库数据
  const [originLibraryTree, setOriginLibraryTree] = useState<any>([])
  // 添加分组弹框是否显示
  const [addGroupVisible, setAddGroupVisible] = useState(false)
  // 添加分组弹框默认值
  const [editGroupData, setEditGroupData] = useState({})
  // 添加过程库弹框是否显示
  const [addProcessLibraryVisible, setAddProcessLibraryVisible] = useState(false)
  // 添加过程库弹框默认值
  const [addProcessLibraryData, setAddProcessLibraryData] = useState({})

  const changeList = useCallback((item:any, rootId?:any) => {
    item.forEach((ele:any) => {
      ele.rootId = rootId || ele.id
      originLibraryList.push(ele)
      if (ele.children && ele.children.length > 0) {
        changeList(ele.children, ele.rootId)
      }
    })
  }, [originLibraryList])

  const selectGroupListAndTableNum = useCallback(() => {
    API.getLibrarTree().then((res:any) => {
      const relationalGraphTreeNodeDTOList = res.data
      relationalGraphTreeNodeDTOList.forEach((item:any) => { // 关联图片加parentId
        item.parentId = item.parentId === '0' ? '关联图谱' : item.parentId
        item.subRoot = '关联图谱'
        if (item.type === null) {
          item.type = 'RG'
        }
      })

      let data = [
        {
          id: '关联图谱',
          name: '关联图谱',
          parentId: '过程库',
          subRoot: '关联图谱',
          type: 'C'
        },
        ...relationalGraphTreeNodeDTOList,
      ]

      // 处理返回数据为树形数据
      let originLibraryTree = handleTree(data, 'id', 'parentId', 'children', '过程库')
      setOriginLibraryTree(originLibraryTree)      
      changeList(originLibraryTree)
    })
  }, [changeList])

  useEffect(() => {
    if (originLibraryTree.length === 0) {
      selectGroupListAndTableNum()
    }
  })
  
  const onInputChange = (e:any) => {
    setSearchValue(e.target.value)
  }
  const addNewGroup = () => {
    setAddGroupVisible(true)
    let data:any = null
    setEditGroupData(data)
  }

  const addProcessLibrary = (param:any) => {
    setAddProcessLibraryVisible(true)
    setAddProcessLibraryData(param)
  }
  const editGroup = (param:any) => {
    setAddGroupVisible(true)
    setEditGroupData(param)
  }

  // 树结构更新
  const updateTree=useCallback(() => {
    selectGroupListAndTableNum()
  }, [selectGroupListAndTableNum])

  useEffect(() => {
    if (saveGraphNum > 0) {
      updateTree()
    }
  }, [saveGraphNum, updateTree])

  
  
  const closeAddProcessLibraryModal = () => {
    setAddProcessLibraryVisible(false)
  }
  const closeEditGroupModal = () => {
    setAddGroupVisible(false)
  }

  return (
      <div className={styles['processlibrarylist-container']}>
        <div className={styles['list-header']}>
          <div className={styles['title']}>图谱列表</div>
        </div>
        <div className={styles['list-main-warp']}>
          <div className={styles['list-action-warp']}>
            <div className={styles['header-search-warp']}>
              <Input.Search placeholder="关键字搜索" onChange={onInputChange}/>
            </div>
            <div className={styles['header-action-btn-warp']}>
              <Button type="primary" ghost className={styles['add-group']} size="small" 
              icon={<img src={addGroupSvg} alt="explore" />} onClick={addNewGroup}>
                添加分组
              </Button>
              <Button type="primary" ghost icon={<img src={addGroupSvg} alt="explore" />}
                size="small" onClick={() => {addProcessLibrary(null)}}
                >
                添加过程库
              </Button>
            </div>
            {
              treeTopShow&&
              <div v-if="treeTopShow"className={styles['top']}>
                <img src={doubleArrowSvg} alt="explore" />
              </div>
            }
          </div>
          <div className={styles['tree']}>
            <OriginLibraryTree 
              originLibraryTree={originLibraryTree}
              originLibraryList={originLibraryList}
              searchValue={searchValue}
              addProcessLibrary={addProcessLibrary}
              editGroupModal={editGroup}
              updateTree={updateTree}
              >
            </OriginLibraryTree>
          </div>
        </div>
        {/* 添加过程库弹框 */}
        {
          addProcessLibraryVisible&&
          <AddProcessLibrary 
          addProcessLibraryVisible={addProcessLibraryVisible} 
          addProcessLibraryData={addProcessLibraryData}
          updateTree={updateTree}
          closeAddProcessLibraryModal={closeAddProcessLibraryModal}
          >
          </AddProcessLibrary>
        }
        {/* 添加分组 */}
        {addGroupVisible&&
          <EditGroup
          addGroupVisible={addGroupVisible}
          editGroupData={editGroupData}
          updateTree={updateTree}
          closeEditGroupModal={closeEditGroupModal}
          ></EditGroup>
        }
      </div>
  )
}


