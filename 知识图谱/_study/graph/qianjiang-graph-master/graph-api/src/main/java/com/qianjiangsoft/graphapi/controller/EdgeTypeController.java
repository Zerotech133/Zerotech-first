package com.qianjiangsoft.graphapi.controller;

import com.qianjiangsoft.graphapi.dto.EdgeTypeReturnDTO;
import com.qianjiangsoft.graphapi.param.CreateEdgeTypeParam;
import com.qianjiangsoft.graphapi.service.EdgeTypeService;
import com.qianjiangsoft.graphcore.param.UpdateEdgeTypeParam;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * @author ulmarr8861
 * @date 2022/3/30
 */
@RequestMapping("/edgeType")
@RestController
@RequiredArgsConstructor
public class EdgeTypeController {
    private final EdgeTypeService edgeTypeService;

    @PostMapping("/create")
    public void create(@RequestBody CreateEdgeTypeParam param) {
        edgeTypeService.createEdgeType(param);
    }

    @GetMapping("/select/bySpaceId")
    public List<EdgeTypeReturnDTO> selectEdgeTypeListBySpaceId(@RequestParam String spaceId) {
        return edgeTypeService.selectEdgeTypeListBySpaceId(spaceId);
    }

    @PostMapping("/update")
    public void update(@RequestBody UpdateEdgeTypeParam param){
        edgeTypeService.updateEdgeTypeById(param);
    }

    @PostMapping("/delete")
    public void delete(@RequestParam("id") String id) {
        edgeTypeService.deleteEdgeTypeById(id);
    }

    @PostMapping("/batchCreate")
    public void batchCreate(@RequestBody List<CreateEdgeTypeParam> edgeTypeList) {
        edgeTypeService.batchCreate(edgeTypeList);
    }

    @PostMapping("/deleteAllEdgeType")
    public void deleteAllEdgeType() {
        edgeTypeService.deleteAllEdgeTpe();
    }

}
