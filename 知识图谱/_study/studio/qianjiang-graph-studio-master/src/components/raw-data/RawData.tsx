import React, { useCallback, useEffect, useState } from "react"
import { getUrlParam } from "@/utils"
import store from "@/redux/store"
import { setRawDataVisible } from "@/redux/action"
import { CloseOutlined } from '@ant-design/icons';
import './RawData.scss'
import { queryEdgeApi } from "@/api/chart"
import { message, Modal } from "antd"

type props = {}
export const RawData: React.FC<props> = () => {
  const closeMask = () => {
    console.log("close")
    store.dispatch(setRawDataVisible({ visible: false }))
  }
  const [visible, setVisible] = useState(store.getState().rawDataVisible)
  store.subscribe(() => {
    setVisible(store.getState().rawDataVisible)
  })

  const [edgeList, setEdgeList] = useState<any[]>(store.getState().edgesData)
  const handleEdgeChange = (index: number) => {
    if (edgeList[index].selected === true) {
      return
    }
    const list = [...edgeList]
    list.forEach(item => {
      item.selected = false
    })
    list[index].selected = true
    setEdgeList(list)
  }
  const handleListClick = (e: any) => {
    e.stopPropagation()
  }

  const queryByMergeEdge = useCallback(() => {
    const activeEdge = edgeList.find(item => item.selected === true)
    let { source, target, edgeTypeName } = activeEdge._cfg.model
    let param = { 'sourceId': source, 'destinationId': target, 'edgeTypeName': edgeTypeName }
    queryEdgeApi(param).then(res => {
      const dataIds = res.data.map((item: any) => {
        return item.propertyList.dataId
      })
      const iframeContent = (document.getElementById("iframe") as HTMLIFrameElement).contentWindow as WindowProxy
      iframeContent.postMessage(
        { list: dataIds, type: "getTableInfo" },
        `${window.location.protocol}//${window.location.hostname}:${getUrlParam("port")}/graph-raw-data`
      )
    })
  }, [edgeList])

  useEffect(() => {
    if (edgeList.length > 0) {
      queryByMergeEdge()
      console.log(edgeList)
    }
  }, [edgeList, queryByMergeEdge])

  useEffect(() => {
    if (visible) {
      let list: any = [...store.getState().edgesData]
      list[0].selected = true
      setEdgeList(list)
    }
  }, [visible])

  useEffect(() => {
    window.addEventListener("message", handleMessage);
    return () => {
      window.removeEventListener("message", handleMessage);
    };
  }, []);

  const handleMessage = (event: MessageEvent) => {
    console.log(event.data);
    const message = event.data
    if (message.type === 'save') {
      setProcessVisible(true)
    }
  };

  const [processVisible, setProcessVisible] = useState(false)
  // 关闭弹窗
  const handleCancel = () => {
    setProcessVisible(false)
  }

  // 表单确认
  const handleProcessConfirm = () => {
    // 通知子页面保存事件
    const iframeContent = (document.getElementById("processLibraryIframe") as HTMLIFrameElement).contentWindow as WindowProxy
    iframeContent.postMessage(
      { type: "saveConfirm" },
      `${window.location.protocol}//${window.location.hostname}:${getUrlParam("port")}/add-process-library`
    )
    message.success('操作成功')
    setProcessVisible(false)
  }

  return (
    <div
      tabIndex={-1}
      className={`raw-data-container ${visible ? 'raw-data-container-visible' : 'raw-data-container-hide'}`}
      style={visible ? { left: 0 } : { left: "9999px" }}>
      <div className="main-container">
        <div className="raw-data-header">
          <div className="raw-data-header-title">原始数据展示</div>
          <CloseOutlined className="raw-data-header-close" onClick={closeMask} />
        </div>
        <div className="raw-data-body">
          <div className="edge-list" onClick={handleListClick}>
            {
              edgeList.map((item: any, index: number) => (
                <div className="edge-list-item" key={index}>
                  <div className="item-content">
                    <div className="source-node">
                      <img src={`${item._cfg?.sourceNode._cfg.model.iconPath}`} alt="类别" />
                    </div>
                    <div
                      className={`line-container ${item.selected ? 'selected' : 'unselected'}`}
                      onClick={() => handleEdgeChange(index)}>
                      <div>{`${item._cfg?.model.edgeTypeName}`}</div>
                      <div className="line"></div>
                      <div>{`强度：${item._cfg?.model.strength}\n次数：${item._cfg?.model.count}`}</div>
                    </div>

                    <div className="target-node">
                      <img src={`${item._cfg?.targetNode._cfg.model.iconPath}`} alt="类别" />
                    </div>
                  </div>
                  <div className="item-name">
                    <div className="source-name">{item._cfg?.sourceNode._cfg.originStyle.label.text}</div>
                    <div className="target-name">{item._cfg?.targetNode._cfg.originStyle.label.text}</div>
                  </div>
                </div>
              ))
            }

          </div>
          <iframe
            title="图谱原始数据"
            className="raw-data-iframe"
            id="iframe"
            src={`${window.location.protocol}//${window.location.hostname}:${getUrlParam("port")}/graph-raw-data`}>
          </iframe>
        </div>
      </div>

      {/*保存过程库弹窗*/}
      <Modal
        title="保存过程库"
        visible={processVisible}
        onCancel={handleCancel}
        onOk={handleProcessConfirm}
        width={1000}
        wrapClassName="add-process-library-modal"
      >

        <iframe
          title="1"
          className="add-process-library-iframe"
          id="processLibraryIframe"
          src={`${window.location.protocol}//${window.location.hostname}:${getUrlParam("port")}/add-process-library`}>
        </iframe>
      </Modal>
    </div>
  )
}
