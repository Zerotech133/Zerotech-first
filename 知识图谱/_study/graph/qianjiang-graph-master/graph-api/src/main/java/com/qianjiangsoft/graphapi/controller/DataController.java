package com.qianjiangsoft.graphapi.controller;

import com.qianjiangsoft.graphapi.param.*;
import com.qianjiangsoft.graphapi.service.DataService;
import com.qianjiangsoft.graphapi.service.ImportService;
import com.qianjiangsoft.graphcore.constants.ResultCode;
import com.qianjiangsoft.graphcore.engine.SpaceContextHolder;
import com.qianjiangsoft.graphcore.exception.BusinessException;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * @author ulmarr8861
 * @date 2022/3/16
 */
@RestController
@RequestMapping(value = "/data")
@RequiredArgsConstructor
public class DataController {

    @Autowired
    ImportService importService;

    @Autowired
    DataService dataService;

    @PostMapping("/vertex/create")
    public void createSpace(@RequestBody BatchCreateVertexParam vertexParam) {
        dataService.createVertex(vertexParam.getVertexParamList(), vertexParam.getTagName());
    }

    @PostMapping("/edge/create")
    public void createSpace(@RequestBody BatchCreateEdgeParam edgeParam) {
        dataService.createEdge(edgeParam.getEdgeParamList(), edgeParam.getEdgeTypeName());
    }

    @PostMapping("/clearData")
    public void clearSpaceData() {
        dataService.clearSpaceData();
    }

    @PostMapping("/syncSpaceInfo")
    public void syncSpaceInfo() {
        dataService.syncSpaceInfo(SpaceContextHolder.getSpaceId());
    }

    @PostMapping("/import/example")
    public void importExampleData(){
        importService.importExampleData();
    }

    @PostMapping("/vertex/update")
    public void updateVertex(@RequestBody UpdateVertexParam vertexParam) {
        dataService.updateVertex(vertexParam);
    }

    @PostMapping("/vertex/merge")
    public void mergeVertex(@RequestBody MergeVertexParam vertexParam) {
        dataService.mergeVertex(vertexParam);
    }

    @PostMapping("/edge/delete/batch")
    public void deleteEdgeList(@RequestBody List<DeleteEdgeParam> paramList) {
        dataService.deleteEdgeList(paramList);
    }

    @PostMapping("/edge/deleteByProperty")
    public void deleteEdgeList(@RequestBody DeleteEdgeByPropertyParam param) {
        dataService.deleteEdgeByProperty(param);
    }



    @PostMapping("/vertex/delete/{id}")
    public void mergeVertex(@PathVariable String id) {
        dataService.deleteVertexById(id);
    }

    /**
     * 随机数据导入指定表空间
     *
     * @param templateId 模板Id[schema_template表]
     * @param sampleSize 创建随机点的数量
     * @param edgeNum 创建单个点的边数量
     * @param spaceId 导入数据到指定的图空间Id[graph_space表]
     * @return java.lang.String
     * @author huangsanbao
     * @date 2022/5/07 14:51
     * @version v1.0
     */
    @PostMapping("/import/randomData")
    public void importRandomData(@RequestParam(value="templateId",required = false) Integer templateId,@RequestParam("sampleSize") long sampleSize
            ,@RequestParam("edgeNum") int edgeNum,@RequestParam("spaceId") String spaceId){
        if(templateId==null) {
            templateId=2;
        }
        if(sampleSize==0){
            sampleSize=100L;
        }
        if(sampleSize<edgeNum){
            throw new BusinessException(ResultCode.PARAM_ERROR, "边数量已大于点数量，建议边数量控制在10以内！");
        }
        if(edgeNum==0 || edgeNum>100){
            edgeNum=10;
        }
        if(edgeNum>sampleSize){
            edgeNum=(int)sampleSize-1;
        }
        importService.importRandomTestData(templateId,sampleSize,edgeNum, spaceId);
    }
}
