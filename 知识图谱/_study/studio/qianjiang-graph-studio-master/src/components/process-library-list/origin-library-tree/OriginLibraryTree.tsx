import React, {useState,useEffect,useRef} from "react"
import {Tree, Popconfirm} from "antd"
import { CaretDownOutlined } from '@ant-design/icons';
import styles from './OriginLibraryTree.module.scss'
import associateImageSvg from '../../../assets/svg/associate-image.svg'
import editSvg from '../../../assets/svg/edit.svg'
import deleteSvg from '../../../assets/svg/delete.svg'
import API from '../../../utils/api'
import { ProcessLibraryImage } from '../process-library-image'


type props = {
  originLibraryTree: Array<any>,
  originLibraryList: Array<any>
  searchValue: string,
  editGroupModal: (data: any) => void,
  addProcessLibrary: (data: any) => void,
  updateTree: () => void,
}

export  const OriginLibraryTree: React.FC<props> = (
  { originLibraryTree,
    originLibraryList,
    searchValue,
    editGroupModal,
    addProcessLibrary,
    updateTree
  }) => {

  const [selectedKeys, setSelectedKeys] = useState<any>([])
  const [multiple, setMultiple] = useState(false)
  const [target, setTarget] = useState<any>([])
  const [imageList,setimageList] = useState<any>([])
  let [libraryId,setLibraryId] = useState('')

  useEffect(() => {
    const keydownEvent = (e: any) => {
      if (e.key === 'Control') {
        setMultiple(true)
      }
    }
    const keyupEvent = (e: any) => {
      if (e.key === 'Control') {
        setMultiple(false)
      }
    }
    window.addEventListener('keydown',keydownEvent)
    window.addEventListener('keyup',keyupEvent)
    return () => {
      window.removeEventListener('keydown', keydownEvent)
      window.removeEventListener('keyup', keyupEvent)
    }
  })
  const dataRef = useRef()
    useEffect(() => {
      dataRef.current = imageList
    }, [imageList])

  const nodeSelect = (selectedKeys:any, e:any) => {
    setSelectedKeys(selectedKeys)
    const tmp = originLibraryList.filter(item => {
      return item.id === selectedKeys[0] && item.type === 'RG'
    })
    setTarget(tmp)
    if (tmp.length > 0) {
      setLibraryId(tmp[0].id)
      const param = {
        libraryId: tmp[0].id,
        pageNum: 0,
        pageSize: 50,
      }
      API.getAssociateImage(param).then((req:any) => {
        setimageList(req.data.list);
      })
    }
  }
  const removeData = (id:string,type:string) => {
    // 删除库
    if (type === 'RG') {
      API.deleteProcess(id).then(() => {
        updateTree()
      })
    } else {// 删除分组
      API.deleteGroup(id).then(() => {
        updateTree()
      })
    }
  }

  const editProcessLibrary = (id:string,children:any) => {
    API.viewGroup(id).then((req:any) => {
      if (req?.data) {
        addProcessLibrary(req.data)
      }
    })
  }
  // 编辑分组
  const editGroup = (id:string,name:string,parentId:string) => {
    const param = {
      id,
      type: 'RG',
      name,
      parentId
    }
    editGroupModal(param)
  }
  // 树节点拖拽
  const onDrop = (info:any) => {
    const dropKey = info.node.key // 放置列id
    const dragKey = info.dragNode.key // 被拖拽列id
    const types = ['P', 'S', 'G', 'RG'] // 过程库 沉淀库 生成库的type
    let dropItem:any // 放置点item详情
    let dragItem:any // 拖拽列item详情
     
    let dragGroupId:any // 被拖拽节点的分组

    originLibraryList.forEach((item:any) => {
      if (item.id === dragKey) {
        dragGroupId = item.parentId
        dragItem = item
      }
      if (item.id === dropKey) {
        dropItem = item
      }
    })
    let flag1
    let flag2
    if (dropItem) {
      flag1 = types.indexOf(dropItem.type) > -1 // 节点不能拖拽到库节点下
      flag2 = dropItem.subRoot !== dragItem.subRoot // 拖拽的2列次根节点需要是同一个次根节点
      
    }
    if (flag1 || flag2 || !dropItem || !dragItem) {
      return
    }
    if (types.indexOf(dragItem.type) > -1) { // 被拖拽的节点是库
      if (selectedKeys.length > 1) { // 拖拽多个库
        const idList: any = []
        
        originLibraryList.forEach((item:any) => {
          if (item.parentId === dragGroupId && selectedKeys.indexOf(item.id) > -1) {
            idList.push(item.id)
          }
        })
        if (idList.length > 0) {
          const param = {
            idList: idList.join(','),
            groupId: dropKey
          }
          API.updateProcessList(param).then((req: any) => {
            setMultiple(false)
            updateTree()
          })
        } else {
          const param = {
            id: dragKey,
            groupId: dropKey
          }
          API.updateProcess(param).then((req: any) => {

            updateTree()
          })
        }
      } else {
        const param = {
          id: dragKey,
          groupId: dropKey
        }
        
        API.updateProcess(param).then((req: any) => {
     
          updateTree()
        })
      }
      
    } else if (dragItem.type === 'N') { // 被拖拽的节点是分组
      const param = {
        id: dragKey,
        parentId: dropKey
      }
      API.updateGroup(param).then((req: any) => {
        updateTree()
      })
    }
  }

  const renderTreeNodes = (data:any) => {
    return data.map((item:any) => {
      let {id, name, tableCount, type, children, parentId} = item

      return (
        <Tree.TreeNode key={item.id} className={styles['tree-item']} title={
          <div className={selectedKeys.join() === id ? styles['node-selected'] : ''}>
            <span className={styles['text']} title={type==='RG'?`${name}(${tableCount})`:`${name}`}>
              {
                item.type === 'RG' &&
                <img 
                src={associateImageSvg} 
                alt="associateImage" 
                style={{width: '12px',height: '12px',display: 'inline-block'}}/>
              }
              <span className={styles['title']}>
                {/* 匹配上搜索文字的字段高亮处理 */}
                <span className={styles['title-content']} >
                  {
                    name.indexOf(searchValue) > -1 &&
                    <>
                      <span>{name.slice(0,name.indexOf(searchValue))}</span>
                      <span
                      style={{color: '#f50',fontWeight: 'bolder'}}
                      >
                        {name.slice(name.indexOf(searchValue),name.indexOf(searchValue)+searchValue.length)}
                      </span>
                      <span >
                        {name.substr(name.indexOf(searchValue)+searchValue.length)}
                      </span>
                    </>
                  }
                  {
                    name.indexOf(searchValue) === -1 &&
                    <span>
                      {name}
                    </span>
                  }
                </span>
                {type==='RG'&&<span >({tableCount || 0})</span>}
              </span>
            </span>
            <span  className={styles['icon']}>
              {
                type !== 'C' && 
                <span>
                  <img 
                  src={editSvg} 
                  alt="editImage" 
                  onClick={() => {  type === 'RG'? editProcessLibrary(id,children):editGroup(id,name,parentId)}}
                  style={{width: '12px',height: '12px',display: 'inline-block'}}/>
                </span>
              }
              
              <Popconfirm
              title="确认删除此数据项吗？"
              onConfirm={() => {removeData(id,type)}}
              >
                {
                  (!(tableCount>0 || (children?.length) || name ==='关联图谱'))&&
                  <img 
                  src={deleteSvg} 
                  alt="deleteImage" 
                  style={{width: '12px',height: '12px',display: 'inline-block'}}/>
                }
              </Popconfirm>
            </span>
          </div>
        }>
            {item.children?.length&&renderTreeNodes(item.children)}
        </Tree.TreeNode>
      )
    })
  }
  
  // 图谱列表请求 公共
  const updataList = (id: any,keyword?: any) => {
    const param = {
      libraryId: id,
      pageNum: 0,
      pageSize: 50,
      keyword: keyword
    }
    API.getAssociateImage(param).then((req:any) => {
      setimageList(req.data.list);
    })
  }
  // 编辑后更新图谱列表
  const updataImageList = (val:any) => {
    updataList(val)
    updateTree()
  }
  // 搜索请求图谱列表
  const searchImageList = (id:any,keyword:any) => {
    updataList(id,keyword)
  }

  return (
    <div>
      <div className={styles['process-library-tree']}>
        {originLibraryTree.length &&
          <Tree 
          defaultExpandAll
          draggable
          multiple={multiple}
          showLine
          switcherIcon={<CaretDownOutlined />}
          onSelect={nodeSelect} 
          onDrop={onDrop}
          >
            {renderTreeNodes(originLibraryTree)}
          </Tree>
        }
      </div>
      {target.length > 0 && 
        <ProcessLibraryImage
        updataImageList={ updataImageList }
        searchImageList = { searchImageList }
        libraryId = {libraryId}
        imageList = { imageList }>
          </ProcessLibraryImage>}
    </div>
  )
}


