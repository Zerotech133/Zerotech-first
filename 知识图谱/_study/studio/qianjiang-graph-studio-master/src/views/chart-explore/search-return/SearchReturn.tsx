import {Modal, Select, Form, Button, Input, InputNumber, message } from 'antd'
import React, {useState, useEffect} from 'react'
import  './SearchReturn.scss'
import { DebounceSelect } from '@/components'
import { findNodeApi, searchReturnApi, findById } from "@/api/chart"
import { EdgeTypeItem } from '@/views/chart-explore/form-items/FormItems'
import { dealNodes, dealEdges } from '@/utils'
import store from '@/redux/store/index'
import { setSearchNodes } from '@/redux/action'

const { Option } = Select

type props = {
  visible:boolean,
  edgeTypeNameList: any,
  selectedNodes: any,
  historyData: any,
  openModal: (flag: boolean) => void,
  mergeData: (data: any) => void
}

const SearchReturn:React.FC<props> = ({visible, edgeTypeNameList, selectedNodes, historyData, openModal, mergeData}) => {
  const [form] = Form.useForm();
  const [vertexlist, setVertexList] = useState<any>([])
  const [loading, setLoading] = useState(false)
  const [taskName,setTaskName] = useState("")
  // 录入历史记录参数
  useEffect(() => {
    if (historyData && historyData.type === 'findLoop') {
      const param = JSON.parse(historyData.param)
      const {vertexIdList, edgeTypeNameList, maxStep, direction,taskName} = param
      // vertexIdList 处理成key lable
      let list=[]
      vertexIdList?.forEach((item: any)=> {
        let param={} as any
        if (item.id) {
          param={id:item.id}
        }else{
          param={id:item}
        }
        let name, iconPath
        findById(param).then((res: any) => {
          name=res.data.name
          iconPath='/api/file'+res.data.tag.iconPath
          list.push({name,iconPath,key:param.id})
        }).then((res:any)=>{
          if (list.length===vertexIdList.length) {
            const vertexIdListData = list?.map((list: any)=> {
             const {name,iconPath,key}=list
                return {
                  key:key,
                  label: (
                    <div  style={{display:"flex",alignItems: "center"}}>
                    {iconPath&&
                    <img style={{width:"15px",height:"15px",marginRight: "5px"}} src={iconPath} alt="" />
                    }
                    <span>{name}</span>
                  </div>
                  )
                }
            })
            setVertexList(vertexIdListData)
            form.setFieldsValue({
              taskName,
              vertexIdList: vertexIdListData,
              edgeTypeNameList,
              maxStep,
              direction
            })
          }
        })
      });
    }
  }, [historyData, form])
  useEffect(() => {
    if (visible) {
      if (historyData && historyData.type === 'findLoop')return
      let time="查找回路-"+(new Date()).toLocaleString()
      setTaskName(time)
      form.setFieldsValue({taskName:time})
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible])
  const closeModal = () => {
    openModal(false)
    if (loading) {
      setLoading(false)
    }
  }

  const onFinish = (param: any) => {
    setLoading(true)
    param.vertexIdList = vertexlist?.map((item: any) => {
      return item.key
    })
    param.edgeTypeNameList = param.edgeTypeNameList.filter((value: string) => value!=='all')
    searchReturnApi(param).then((res) => {
      if (!res.data) {
        message.warn('未查询到数据')
        return
      }
      const {pathList, vertexList} = res.data
      if (!pathList.length || !vertexList.length) {
        message.warn('未查询到扩展数据')
        return
      }
      openModal(false)
      const searchNodes = param.vertexIdList
      store.dispatch(setSearchNodes({searchNodes}))
      let edgeList = [...pathList.flat(),...pathList.flat()]
      mergeData({
        edges: dealEdges(edgeList),
        nodes: dealNodes(vertexList),
        highlight: true
      })
    }).finally(() => {
      setLoading(false)
    })
  }
  const findNode = (value: string) => {
    return new Promise(((resolve, reject) => {
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

      })
    }))
  }
  useEffect(() => {
    let values: any[] = []
    selectedNodes.forEach((it: any) => {
      const {id, style, type, iconPath}: any = it.getModel()
      values.push({
        label: (
          <div  style={{display:"flex",alignItems: "center"}}>
            {iconPath&&
            <img style={{width:"15px",height:"15px",marginRight: "5px"}} src={iconPath} alt="" />
            }
            <span>{style.label.value}({type})</span>
          </div>
        ),
        key: id
      })
    })
    if (values.length)  setVertexList(values)
  }, [selectedNodes])
  const handleEdgeChange = (value: any) => {
    form.setFieldsValue({
      edgeTypeNameList: value
    })
  }
  const handleChange = (values: any) => {
    setVertexList(values)
  }

  return(
    <Modal
    title="查找回路"
    visible={visible}
    className="search-return"
    footer={null}
    width={568}
    maskClosable={false}
    onCancel={closeModal}
    >
      <Form
      labelCol={{ span: 4 }}
      wrapperCol={{ span: 20 }}
      form={form}
      onFinish={onFinish}>
        <Form.Item
          label="任务名称"
          name="taskName"
          initialValue={taskName}
          rules={[{ required: true, message: '请输入任务名称' }]}
        >
          <Input style={{ width: '100%' }} placeholder="请输入任务名称" />
        </Form.Item>
        <Form.Item
          label="实体"
          name="vertexIdList"
          rules={[({ getFieldValue }) => ({
            validator(_, value) {
                if (vertexlist?.length > 0) {
                  return Promise.resolve()
                }
                return Promise.reject(new Error('请选择实体!'));
              }
            })]}>
          <DebounceSelect
            mode="multiple"
            placeholder="请输入关键字"
            allowClear={true}
            fetchOptions={findNode}
            vertexlist={vertexlist}
            onChange={handleChange}
          />
        </Form.Item>
        {/* 边类型 */}
        <EdgeTypeItem edgeTypeNameList={edgeTypeNameList} handleEdgeChange={handleEdgeChange}/>
        <Form.Item label="最大步数" name="maxStep" rules={[{required: true, message: '请输入最大步数'}]}>
          <InputNumber style={{width: '100%'}} min={1} placeholder="请输入最大步数"/>
        </Form.Item>
        <Form.Item
          label="连接方向"
          name="direction"
          rules={[{ required: true, message: '请选择连接方向' }]}
        >
          <Select allowClear={true} placeholder="请选择连接方向">
            <Option value="forward">有向</Option>
            <Option value="undirected">无向</Option>
          </Select>
        </Form.Item>
        <Form.Item wrapperCol={{ offset: 8, span: 16 }}>
          <Button size='middle' onClick={closeModal}>取消</Button>
          <Button size='middle' type="primary" htmlType="submit" style={{margin: '0 20px'}} loading={loading}>查找</Button>
      </Form.Item>
        {loading&&<div style={{color:"red", textAlign:'center', fontSize:"12px", marginTop:"6px"}}>若当前功能运行时间较长，可关闭页面，后续进入分析记录页面查看运行结果</div>}
      </Form>
    </Modal>
  )
}
export default SearchReturn