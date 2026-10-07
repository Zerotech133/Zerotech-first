import React, { useState, useEffect } from 'react'
import {Modal, Select, Form, Button, InputNumber, message } from 'antd'
import  './SearchReturnWeb.scss'
import { EdgeTypeItem } from '@/views/chart-explore/form-items/FormItems'
import {Algorithm} from "@antv/g6";
import store from '@/redux/store/index'
import { setSearchNodes } from '@/redux/action'

const { Option } = Select
// @ts-ignore
const { detectAllCycles } = Algorithm

type props = {
  visible:boolean,
  edgeTypeNameList: any,
  selectedNodes: any,
  data: any,
  openModal: (flag: boolean) => void,
  mergeData: (data: any) => void
}

const SearchReturnWeb:React.FC<props> = ({visible, edgeTypeNameList, selectedNodes, data, openModal, mergeData}) => {
  const [form] = Form.useForm();
  const [vertexIdList, setVertexIdList] = useState<any>([])
  // 关闭弹框
  const closeModal = () => {
    openModal(false)
  }
  // 获取回路所有边
  const getEdgesList = (arr: any,step:number, maxStep: number, vertexIdFirst: string) =>{
    if (step < maxStep) {
      let newArr: any = []
      arr.forEach((item: any) => {
        let originItem = [...item]
        let lastNodeId = item[item.length - 1]
        // 环退出，没有下一级的路径退出
        if (lastNodeId === vertexIdFirst || item.length < step + 1) return
        data.edges.forEach((edge: any) => {
          const {source , target} = edge
          if (step === 1) {
            if (source === lastNodeId && (originItem.indexOf(target) === -1)) {
              // 下级多个点，数组添加数据
              if (item.length < step + 2) {
                item.push(target)
              } else {
                newArr.push([...originItem,target])
              }
            } else if(target === lastNodeId && (originItem.indexOf(source) === -1)) {
              if (item.length < step + 2) {
                item.push(source)
              } else {
                newArr.push([...originItem,source])
              }
            }
          } else {
            if (source === lastNodeId && (originItem.indexOf(target) === -1|| (target === vertexIdFirst))) {
              if (item.length < step + 2) {
                item.push(target)
              } else {
                newArr.push([...originItem,target])
              }
            } else if(target === lastNodeId && (originItem.indexOf(source) === -1||(source === vertexIdFirst))) {
              if (item.length < step + 2) {
                item.push(source)
              } else {
                newArr.push([...originItem,source])
              }
            }
          }
        })
      })
      newArr.forEach((item: any) => {arr.push(item)})
      step ++
      getEdgesList(arr,step, maxStep, vertexIdFirst)
    }
  }

  const onFinish = (param: any) => {
    const { direction, maxStep, edgeTypeNameList } = param
    param.edgeTypeNameList = param.edgeTypeNameList.filter((value: string) => value!=='all')
    let path: any = []
    if (direction === 'forward') { // 有向
      path = detectAllCycles(data, true,vertexIdList)
    } else { // 无向
      // api获取边
      // path = detectAllCycles(data, false,vertexIdList)
      // 自己计算获取的边
      // 以vertexIdList[0]为顶点，查询所有圈
      let arr: any = []
      const vertexIdFirst = vertexIdList[0]
      let sourceTargets = data.edges.map((item: any) => item.source + '-' + item.target)
      // 路径===1
      data.edges.forEach((edge: any) => {
        const {source , target} = edge
        if (source === vertexIdFirst) {
          arr.push([vertexIdFirst, target])
        } else if (target === vertexIdFirst) {
          arr.push([vertexIdFirst, source])
        }
      })
      // 路径 === 2
      if (maxStep > 1) {
        data.edges.forEach((edge: any) => {
          const {source , target} = edge
          const targetSource = target + '-' + source
          if (source === vertexIdFirst && target!==vertexIdFirst && sourceTargets.includes(targetSource)) {
              arr.push([source, target, source])
          } else if (target === vertexIdFirst && source!==vertexIdFirst && sourceTargets.includes(targetSource)) {
            arr.push([target, source, target])
          }
        })
      }
      if (maxStep > 2) {
        getEdgesList(arr, 1, maxStep, vertexIdFirst)
      }
      // arr与path格式一致
      arr = arr.filter((item: any) => item[0] === item[item.length - 1])
      arr = arr.map((item: any) => {
                let obj:any = {}
                item.forEach((ele: string, index:number) => {
                  if (index < item.length - 1) {
                    obj[ele] = {id: item[index + 1]}
                  }
                })
                return obj
              })
      // api获取的边与自己计算获取的边合并
      path = [...path, ...arr]
    }
    // 根据实体和最大步数过滤
    path = path.filter((item: any) => {
      let keys = Object.keys(item)
      let num = 0
      // 路径中是否包含所有vertexIdList
      vertexIdList.forEach((id: string) => {
        keys.includes(id) && num++
      })
      return num === vertexIdList.length && keys.length < maxStep + 1
    })
    if (path.length === 0) {
      message.warning('未查询到数据')
      return
    }
    store.dispatch(setSearchNodes({searchNodes: vertexIdList}))
    const edgeLink: any = []
    // 路径source-target格式
    path.forEach((item: any) => {
      for(let key in item) {
        let edge =  key + '-' + item[key].id
        if (!edgeLink.includes(edge)) {
          edgeLink.push(edge)
        }

      }
    })
    let nodesId: any = []
    const edges = []
    data.edges.forEach((edge) => {
      const { source, target, edgeTypeName } = edge
      const sourceTarget = source + '-' + target
      const targetSource = target + '-' + source
      if ((edgeLink.includes(sourceTarget) || edgeLink.includes(targetSource))
          && (edgeTypeNameList.includes(edgeTypeName) || edgeTypeNameList.includes('all'))
         ) {
          edges.push(edge)
          nodesId = [...nodesId, source, target]
      }
    })
    const set: any = new Set(nodesId)
    nodesId = [...set]
    const nodes = data.nodes.filter(node => nodesId.includes(node.id))
    mergeData({
      highlight: true,
      edges,
      nodes
    })
    openModal(false)
  }

  useEffect(() => {
    let values = selectedNodes.map((item: any) => item.getModel().id)
    if (values.length)  {
      setVertexIdList(values)
      form.setFieldsValue({
        vertexIdList: values
      })
    }
  }, [selectedNodes, form])
  const handleEdgeChange = (value: any) => {
    form.setFieldsValue({
      edgeTypeNameList: value
    })
  }
  const handleChange = (values: any) => {
    setVertexIdList(values)
    form.setFieldsValue({
      vertexIdList: values
    })
  }

  return(
    <Modal
    title="查找回路"
    visible={visible}
    className="search-return-web"
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
          label="实体"
          name="vertexIdList"
          rules={[({ getFieldValue }) => ({
            validator(_, value) {
              if (vertexIdList?.length > 0) {
                return Promise.resolve()
              }
              return Promise.reject(new Error('请选择实体!'));
            }
            })]}>
              <Select
                mode="multiple"
                maxTagCount="responsive"
                allowClear
                onChange={handleChange}
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
          <Button size='large' onClick={closeModal}>取消</Button>
          <Button size='large' type="primary" htmlType="submit" style={{margin: '0 20px'}}>查找</Button>
      </Form.Item>
      </Form>
    </Modal>
  )
}
export default SearchReturnWeb